import os
import tempfile

import numpy as np
import SimpleITK as sitk

from .config import TEMP_DIR


class Preprocessor:
    def __init__(
        self,
        hu_window=(100, 700),
        target_spacing=(0.6, 0.6, 0.6),
    ):
        self.hu_window = hu_window
        self.target_spacing = target_spacing

    def run(self, sitk_image: sitk.Image) -> sitk.Image:

        pixel_type = sitk_image.GetPixelID()
        if pixel_type == sitk.sitkInt16:
            result = sitk.Clamp(
                sitk_image,
                lowerBound=float(self.hu_window[0]),
                upperBound=float(self.hu_window[1]),
                outputPixelType=sitk.sitkInt16,
            )
        else:
            result = sitk.Clamp(
                sitk_image,
                lowerBound=float(self.hu_window[0]),
                upperBound=float(self.hu_window[1]),
                outputPixelType=sitk.sitkFloat32,
            )

        orig_spacing = result.GetSpacing()
        if any(abs(a - b) > 0.001 for a, b in zip(orig_spacing, self.target_spacing)):
            result = self._resample(result)

        if pixel_type != sitk.sitkInt16:
            result = self._normalize_01_numpy(result)

        return result

    def to_nifti_temp(self, sitk_image: sitk.Image, prefix="cta_") -> str:
        os.makedirs(TEMP_DIR, exist_ok=True)
        fd, path = tempfile.mkstemp(suffix=".nii.gz", prefix=prefix, dir=TEMP_DIR)
        os.close(fd)
        sitk.WriteImage(sitk_image, path)
        return path

    def to_numpy(self, sitk_image: sitk.Image) -> tuple:

        arr = sitk.GetArrayFromImage(sitk_image)
        spacing = sitk_image.GetSpacing()
        origin = sitk_image.GetOrigin()
        return arr, spacing, origin

    @staticmethod
    def numpy_to_sitk(arr: np.ndarray, spacing, origin, reference_image=None) -> sitk.Image:
        img = sitk.GetImageFromArray(arr)
        img.SetSpacing(spacing)
        img.SetOrigin(origin)
        if reference_image is not None:
            img.SetDirection(reference_image.GetDirection())
        return img

    def _normalize_01_numpy(self, image: sitk.Image) -> sitk.Image:
        arr = sitk.GetArrayFromImage(image).astype(np.float32, copy=False)
        lo = float(arr.min())
        hi = float(arr.max())
        if hi - lo < 1e-6:
            return image
        arr -= lo
        arr /= (hi - lo)
        result = sitk.GetImageFromArray(arr)
        result.CopyInformation(image)
        return result

    def _resample(self, image: sitk.Image) -> sitk.Image:
        orig_size = np.array(image.GetSize(), dtype=np.float64)
        orig_spacing = np.array(image.GetSpacing(), dtype=np.float64)
        new_spacing = np.array(self.target_spacing, dtype=np.float64)
        new_size = np.round(orig_size * orig_spacing / new_spacing).astype(int)
        resampler = sitk.ResampleImageFilter()
        resampler.SetSize(new_size.tolist())
        resampler.SetOutputSpacing(self.target_spacing)
        resampler.SetOutputOrigin(image.GetOrigin())
        resampler.SetOutputDirection(image.GetDirection())
        resampler.SetInterpolator(sitk.sitkLinear)
        return resampler.Execute(image)

    def cleanup_temp(self, path: str):
        try:
            os.remove(path)
        except OSError:
            pass
