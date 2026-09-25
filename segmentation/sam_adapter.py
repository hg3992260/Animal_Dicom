import os
import sys

import numpy as np
import SimpleITK as sitk

from .config import SAM_MED3D_DIR, SAM_CKPT, DEFAULT_THRESHOLD


def _fmt_note(msg: str):
    """安全打印（避免 GBK 控制台/无 stdout 冻结环境下编码异常中断推理）。"""
    try:
        print(f"[SAMMed3D] {msg}")
    except Exception:
        pass


def _setup_sys_path():
    if SAM_MED3D_DIR not in sys.path:
        sys.path.insert(0, SAM_MED3D_DIR)


class SAMMed3DAdapter:
    def __init__(self, checkpoint_path: str = None, device: str = "cuda"):
        _setup_sys_path()
        os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
        self.checkpoint_path = checkpoint_path or SAM_CKPT
        self.device = device
        self._model = None
        self._loaded = False
        self._emb_cache_key = None   # (id(vol), shape, z0,y0,x0, ...) 定位 cache
        self._emb_cache = None       # (emb, pe) GPU 常驻，加速同区域多点 refine

    def load_model(self):

        if self._loaded:
            return
        import medim

        # 权重缺失时回退到 HuggingFace 自动下载（medim 支持 URL），
        # 便于冻结版 EXE 首次运行自足获取 ~383MB 权重。
        ckpt = self.checkpoint_path
        if not (ckpt and os.path.exists(ckpt)):
            ckpt = "https://huggingface.co/blueyo0/SAM-Med3D/blob/main/sam_med3d_turbo.pth"
            _fmt_note(f"本地权重不存在，改用 HuggingFace 自动下载: {ckpt}")
        self._model = medim.create_model(
            "SAM-Med3D",
            pretrained=True,
            checkpoint_path=ckpt,
        )
        self._model = self._model.to(self.device)
        self._loaded = True

    def ensure_loaded(self):

        if not self._loaded:
            self.load_model()

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    TILE_SIZE = 128
    STRIDE = 64
    FEATHER_RAMP = 20

    def predict_vessel_prior(
        self,
        nifti_path: str,
        num_clicks: int = 1,
    ) -> np.ndarray:

        self.num_clicks = num_clicks
        self.ensure_loaded()
        import torch
        import torchio as tio
        from utils.click_method import \
            get_next_click3D_torch_no_gt as click_fn

        img = sitk.ReadImage(nifti_path)
        subject = tio.Subject(image=tio.ScalarImage.from_sitk(img))
        subject_canonical = tio.ToCanonical()(subject)

        data_tensor = subject_canonical.image.data.float()
        total_shape = subject_canonical.spatial_shape
        D, H, W = total_shape

        full_prob = np.zeros(total_shape, dtype=np.float32)
        full_weight = np.zeros(total_shape, dtype=np.float32)

        tile = self.TILE_SIZE
        stride = self.STRIDE

        z_starts = list(range(0, D, stride))
        y_starts = list(range(0, H, stride))
        x_starts = list(range(0, W, stride))
        total_tiles = len(z_starts) * len(y_starts) * len(x_starts)
        tile_idx = 0

        for z0 in z_starts:
            for y0 in y_starts:
                for x0 in x_starts:
                    tile_idx += 1
                    z1, y1, x1 = min(z0 + tile, D), min(y0 + tile, H), min(x0 + tile, W)
                    slice_z = slice(z0, z1)
                    slice_y = slice(y0, y1)
                    slice_x = slice(x0, x1)

                    actual_shape = (z1 - z0, y1 - y0, x1 - x0)
                    pad_before = (0, 0, 0)
                    pad_after = (tile - actual_shape[0], tile - actual_shape[1], tile - actual_shape[2])

                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()

                    chunk = data_tensor[:, slice_z, slice_y, slice_x].clone()
                    mean = chunk.mean()
                    std = chunk.std()
                    if std > 1e-6:
                        chunk = (chunk - mean) / std
                    if any(p > 0 for p in pad_after):
                        chunk = torch.nn.functional.pad(chunk, (0, pad_after[2], 0, pad_after[1], 0, pad_after[0]))
                    roi = chunk.unsqueeze(0).to(self.device)

                    prob_tile = self._infer_one_tile(roi, click_fn, num_clicks=self.num_clicks)

                    weight_tile = self._feather_weights_3d(actual_shape, pad_after)

                    full_prob[slice_z, slice_y, slice_x] += (
                        prob_tile[:actual_shape[0], :actual_shape[1], :actual_shape[2]]
                        * weight_tile[:actual_shape[0], :actual_shape[1], :actual_shape[2]]
                    )
                    full_weight[slice_z, slice_y, slice_x] += weight_tile[
                        :actual_shape[0], :actual_shape[1], :actual_shape[2]
                    ]

        mask = full_weight > 0
        full_prob[mask] /= full_weight[mask]
        return full_prob

    def _infer_one_tile(self, roi_image, click_fn, num_clicks=1) -> np.ndarray:

        import torch

        pe = self._model.prompt_encoder.get_dense_pe()
        with torch.no_grad():
            image_embeddings = self._model.image_encoder(roi_image)
        p_tm = torch.zeros((roi_image.shape[0], 1) + roi_image.shape[2:], dtype=torch.bool, device=roi_image.device)
        with torch.no_grad():
            prev_low_res_mask = None
            for click_i in range(num_clicks):
                coords, labels = click_fn(p_tm, roi_image, threshold=170)
                pts = coords[0].to(self.device)
                lbs = labels[0].to(self.device)
                sparse, dense = self._model.prompt_encoder(
                    points=(pts, lbs) if pts is not None else None,
                    boxes=None,
                    masks=prev_low_res_mask,
                )
                masks_low, _ = self._model.mask_decoder(
                    image_embeddings=image_embeddings,
                    image_pe=pe,
                    sparse_prompt_embeddings=sparse,
                    dense_prompt_embeddings=dense,
                    multimask_output=False,
                )
                prev_low_res_mask = masks_low
                if click_i < num_clicks - 1:
                    upsampled = torch.nn.functional.interpolate(
                        torch.sigmoid(masks_low.float()),
                        size=(self.TILE_SIZE, self.TILE_SIZE, self.TILE_SIZE),
                        mode="trilinear",
                        align_corners=False,
                    )
                    p_tm = upsampled > 0.5
            low_res_pred = prev_low_res_mask.float()
            low_res_pred = torch.sigmoid(low_res_pred)
            prob = torch.nn.functional.interpolate(
                low_res_pred,
                size=(self.TILE_SIZE, self.TILE_SIZE, self.TILE_SIZE),
                mode="trilinear",
                align_corners=False,
            )
        return prob.squeeze().cpu().numpy()

    @classmethod
    def _feather_weights_3d(cls, actual_shape, pad_after) -> np.ndarray:

        D, H, W = cls.TILE_SIZE, cls.TILE_SIZE, cls.TILE_SIZE
        aD, aH, aW = actual_shape
        r = cls.FEATHER_RAMP
        ramp = np.arange(r, dtype=np.float32) / r
        rev_ramp = ramp[::-1].copy()

        weight = np.ones((D, H, W), dtype=np.float32)

        if aD < D:
            weight[aD:, :, :] = 0.0
            if aD > r:
                weight[aD - r:aD, :, :] *= rev_ramp[:, None, None]
            else:
                weight[:aD, :, :] *= 1.0
        if aH < H:
            weight[:, aH:, :] = 0.0
            if aH > r:
                weight[:, aH - r:aH, :] *= rev_ramp[None, :, None]
        if aW < W:
            weight[:, :, aW:] = 0.0
            if aW > r:
                weight[:, :, aW - r:aW] *= rev_ramp[None, None, :]

        return weight

    # ------------------------------------------------------------------
    # 交互式点 prompt 分割（用户点击 → 3D 正/负点 → 全卷 prob/mask）
    # ------------------------------------------------------------------

    def predict_from_volume(
        self,
        vol_zyx: np.ndarray,
        pts_zyx,
        neg_pts_zyx=None,
        threshold: float = None,
        return_prob: bool = False,
    ):
        """对任意形状 int16/float 体积（z,y,x 序）用点 prompt 交互分割。

        vol_zyx    : (D,H,W) numpy，VR 渲染网格同坐标系（输出 mask 与其同形）。
        pts_zyx    : 正点列表 [(z,y,x), ...]（至少 1 个）。
        neg_pts_zyx: 负点列表 [(z,y,x), ...]（可选）。
        采用「以点击区域为中心的 128³ crop」单轮 sparse prompt（SAM-Med3D 原生用法）。
        返回 (D,H,W) bool mask；return_prob=True 时返回 float prob。
        """
        self.ensure_loaded()
        self._model.eval()
        import torch

        vol = np.asarray(vol_zyx)
        if vol.ndim != 3:
            raise ValueError(f"vol 需为 3D (z,y,x)，得到 {vol.shape}")
        D, H, W = vol.shape
        pos = [tuple(int(v) for v in p) for p in (pts_zyx or [])]
        neg = [tuple(int(v) for v in p) for p in (neg_pts_zyx or [])]
        if not pos:
            raise ValueError("需至少 1 个正点")

        # ---- 计算 128³ 窗口（覆盖所有点；锚=正点质心附近，保证包住全部点） ----
        tile = self.TILE_SIZE
        all_pts = pos + neg
        mins = np.min(all_pts, axis=0)
        maxs = np.max(all_pts, axis=0)
        span = maxs - mins + 1
        # ---- 128³ 窗口策略 ----
        # 点跨度 ≤128：单窗口包含全部点；
        # 跨度 >128：单窗口无法覆盖 → 对每个正点各做一次以其为中心的窗口，取概率最大值合并，
        #            确保每个种子点都被真正使用（此前会丢弃超窗点）。
        def _wstart(center_c, L):
            if L <= tile:
                return 0
            return int(max(0, min(center_c - tile // 2, L - tile)))

        def _infer_window(center, pos_pts, neg_pts):
            """以 center 为锚的 128³ 窗口推理，返回 (z0,y0,x0, prob_crop 或 None, aD,aH,aW)。"""
            z0 = _wstart(center[0], D)
            y0 = _wstart(center[1], H)
            x0 = _wstart(center[2], W)
            z1, y1, x1 = min(z0 + tile, D), min(y0 + tile, H), min(x0 + tile, W)
            aD, aH, aW = z1 - z0, y1 - y0, x1 - x0

            content = vol[z0:z1, y0:y1, x0:x1].astype(np.float32)
            fg = content[content > 0]
            if fg.size:
                mean = float(fg.mean()); std = float(fg.std())
            else:
                mean = float(content.mean()); std = float(content.std())
            if std > 1e-6:
                content = (content - mean) / std
            crop = np.zeros((tile, tile, tile), dtype=np.float32)
            crop[:aD, :aH, :aW] = content

            local_pts, local_lbs = [], []
            for (z, y, x), lb in [(p, 1) for p in pos_pts] + [(p, 0) for p in neg_pts]:
                if z0 <= z < z1 and y0 <= y < y1 and x0 <= x < x1:
                    local_pts.append([z - z0, y - y0, x - x0]); local_lbs.append(lb)
            if not local_pts:
                return z0, y0, x0, None, aD, aH, aW

            pts_t = torch.tensor(local_pts, dtype=torch.float32).unsqueeze(0).to(self.device)
            lbs_t = torch.tensor(local_lbs, dtype=torch.long).unsqueeze(0).to(self.device)
            roi = torch.from_numpy(crop).unsqueeze(0).unsqueeze(0).to(self.device)

            key = (id(vol), vol.shape, z0, y0, x0, aD, aH, aW)
            cached = self._emb_cache if self._emb_cache is not None and self._emb_cache_key == key else None
            with torch.no_grad():
                pe = self._model.prompt_encoder.get_dense_pe()
                if cached is None:
                    image_embeddings = self._model.image_encoder(roi)
                    self._emb_cache = (image_embeddings, pe); self._emb_cache_key = key
                else:
                    image_embeddings, pe = cached
                prev_low_res_mask = torch.zeros(
                    (1, 1, tile // 4, tile // 4, tile // 4), dtype=torch.float, device=self.device,
                )
                sparse, dense = self._model.prompt_encoder(
                    points=(pts_t, lbs_t), boxes=None, masks=prev_low_res_mask,
                )
                low_res_pred, _ = self._model.mask_decoder(
                    image_embeddings=image_embeddings, image_pe=pe,
                    sparse_prompt_embeddings=sparse, dense_prompt_embeddings=dense,
                    multimask_output=False,
                )
                prob128 = torch.sigmoid(low_res_pred.float())
                prob128 = torch.nn.functional.interpolate(
                    prob128, size=(tile, tile, tile), mode="trilinear", align_corners=False,
                )
                prob_crop = prob128[0, 0].cpu().numpy()
            return z0, y0, x0, prob_crop, aD, aH, aW

        full_prob = np.zeros((D, H, W), dtype=np.float32)
        if np.any(span > tile):
            _fmt_note(f"点跨度 {tuple(span)} > 128^3：对 {len(pos)} 个正点分别推理并取最大值合并")
            for p in pos:
                z0, y0, x0, pc, aD, aH, aW = _infer_window(np.array(p), [p], neg)
                if pc is None:
                    continue
                full_prob[z0:z0 + aD, y0:y0 + aH, x0:x0 + aW] = np.maximum(
                    full_prob[z0:z0 + aD, y0:y0 + aH, x0:x0 + aW], pc[:aD, :aH, :aW])
        else:
            center = (mins + maxs) / 2.0
            z0, y0, x0, pc, aD, aH, aW = _infer_window(center, pos, neg)
            if pc is None:
                raise ValueError("窗口内没有有效 prompt 点")
            full_prob[z0:z0 + aD, y0:y0 + aH, x0:x0 + aW] = pc[:aD, :aH, :aW]

        if return_prob:
            return full_prob
        thr = DEFAULT_THRESHOLD if threshold is None else float(threshold)
        return full_prob >= thr
