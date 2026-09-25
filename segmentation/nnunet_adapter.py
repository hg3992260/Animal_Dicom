from __future__ import annotations


class NNUNetAdapter:

    def __init__(self, model_folder: str = None, device: str = "cuda"):
        self.model_folder = model_folder or ""
        self.device = device
        self._predictor = None
        self._loaded = False

    def load_model(self):
        raise NotImplementedError(
            "nnU-Net v2 目前无公开血管分割预训练权重。"
            "请先使用 nnU-Net 训练血管模型，将模型文件夹路径传入后调用。"
        )

    def predict(self, image_array, properties: dict):
        raise NotImplementedError("nnU-Net 模型未就绪")
