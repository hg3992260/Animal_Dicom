import numpy as np
from scipy import ndimage as ndi


class Postprocessor:
    def __init__(
        self,
        min_component_size: int = 500,
        closing_kernel: int = 3,
    ):
        self.min_component_size = min_component_size
        self.closing_kernel = closing_kernel

    def run(self, mask: np.ndarray, probability: np.ndarray = None) -> np.ndarray:

        if probability is not None:
            binary = (probability > 0.5).astype(np.uint8)
        else:
            binary = mask.astype(np.uint8)

        binary = self._remove_small_components(binary, self.min_component_size)
        binary = self._morphological_closing(binary, self.closing_kernel)
        binary = ndi.binary_fill_holes(binary).astype(np.uint8)
        return binary

    @staticmethod
    def _remove_small_components(mask: np.ndarray, min_size: int) -> np.ndarray:
        labeled, num = ndi.label(mask)
        sizes = ndi.sum(mask, labeled, range(1, num + 1))
        keep = sizes >= min_size
        keep_labels = np.where(keep)[0] + 1
        result = np.zeros_like(mask, dtype=np.uint8)
        np.putmask(result, np.isin(labeled, keep_labels), 1)
        return result

    @staticmethod
    def _morphological_closing(mask: np.ndarray, kernel_size: int) -> np.ndarray:
        struct = np.ones((kernel_size, kernel_size, kernel_size), dtype=bool)
        return ndi.binary_closing(mask, structure=struct, iterations=1).astype(np.uint8)

    def centerline(self, mask: np.ndarray) -> np.ndarray:

        from skimage.morphology import skeletonize_3d
        return skeletonize_3d(mask.astype(bool))

    def compute_stats(self, mask: np.ndarray, spacing: tuple) -> dict:

        labeled, num = ndi.label(mask)
        sizes = []
        for i in range(1, num + 1):
            sizes.append(int(np.sum(labeled == i)))
        sizes.sort(reverse=True)

        voxel_volume_mm3 = np.prod(spacing)
        total_volume_mm3 = int(mask.sum()) * voxel_volume_mm3

        return {
            "total_voxels": int(mask.sum()),
            "num_components": num,
            "largest_component": sizes[0] if sizes else 0,
            "component_sizes": sizes[:10],
            "volume_mm3": total_volume_mm3,
            "volume_cm3": total_volume_mm3 / 1000.0,
        }
