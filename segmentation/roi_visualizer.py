from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
import vtk
from vtkmodules.util import numpy_support

from .roi_types import ROIRegionResult, ROIBlock


ROI_OVERLAY_COLOR = (0.15, 1.0, 0.18)
ROI_EDGE_COLOR = (0.75, 1.0, 0.78)


class ROIVisualizer:
    def __init__(self, renderer: vtk.vtkRenderer):
        self.renderer = renderer
        self.image_spacing: Optional[Tuple[float, float, float]] = None
        self.image_origin: Optional[Tuple[float, float, float]] = None
        self.image_direction: Tuple[float, ...] = (
            1.0, 0.0, 0.0,
            0.0, 1.0, 0.0,
            0.0, 0.0, 1.0,
        )
        self.actors: List[vtk.vtkActor] = []
        self.label_actors: List[vtk.vtkActor] = []
        self.label_texts: List[vtk.vtkTextActor3D] = []

    def set_image_geometry(
        self,
        spacing: Tuple[float, float, float],
        origin: Tuple[float, float, float],
        direction: Optional[Tuple[float, ...]] = None,
    ) -> None:
        self.image_spacing = spacing
        self.image_origin = origin
        if direction is not None and len(direction) >= 9:
            self.image_direction = tuple(float(v) for v in direction[:9])
        else:
            self.image_direction = (
                1.0, 0.0, 0.0,
                0.0, 1.0, 0.0,
                0.0, 0.0, 1.0,
            )

    def remove_all(self):
        for actor in self.actors:
            self.renderer.RemoveActor(actor)
        for actor in self.label_actors:
            self.renderer.RemoveActor(actor)
        for text in self.label_texts:
            self.renderer.RemoveActor(text)
        self.actors.clear()
        self.label_actors.clear()
        self.label_texts.clear()

    def show_region_blocks(self, region_results: List[ROIRegionResult]) -> None:
        self.remove_all()

        for region_result in region_results:
            blocks_by_cat = {
                "bone": region_result.bones,
                "vessel": region_result.vessels,
            }
            for tissue_block in region_result.tissues:
                cat = tissue_block.category
                if cat not in blocks_by_cat:
                    blocks_by_cat[cat] = []
                blocks_by_cat[cat].append(tissue_block)

            for category, blocks in blocks_by_cat.items():
                for block in blocks:
                    self._add_block_surface(block, ROI_OVERLAY_COLOR)

    def show_blocks(self, blocks: List[ROIBlock]) -> None:
        self.remove_all()
        for block in blocks:
            self._add_block_surface(block, ROI_OVERLAY_COLOR)

    def _add_block_surface(self, block: ROIBlock, color: tuple) -> None:
        if self.image_spacing is None or self.image_origin is None:
            return
        if block.mask.size == 0 or not np.any(block.mask):
            return

        vtk_image = vtk.vtkImageData()
        shape = block.mask.shape
        vtk_image.SetDimensions(shape[2], shape[1], shape[0])
        vtk_image.SetSpacing(1.0, 1.0, 1.0)
        vtk_image.SetOrigin(0.0, 0.0, 0.0)

        flat = block.mask.astype(np.uint8).ravel(order="C")
        vtk_array = numpy_support.numpy_to_vtk(flat, deep=True, array_type=vtk.VTK_UNSIGNED_CHAR)
        vtk_image.GetPointData().SetScalars(vtk_array)

        contour = vtk.vtkMarchingCubes()
        contour.SetInputData(vtk_image)
        contour.SetValue(0, 0.5)
        contour.ComputeGradientsOff()
        contour.ComputeNormalsOn()
        contour.Update()

        if contour.GetOutput().GetNumberOfPoints() == 0:
            return

        transform = vtk.vtkTransform()
        transform.SetMatrix(self._block_world_matrix(block))

        transform_filter = vtk.vtkTransformPolyDataFilter()
        transform_filter.SetTransform(transform)
        transform_filter.SetInputConnection(contour.GetOutputPort())
        transform_filter.Update()

        smoother = vtk.vtkWindowedSincPolyDataFilter()
        smoother.SetInputConnection(transform_filter.GetOutputPort())
        smoother.SetNumberOfIterations(18)
        smoother.BoundarySmoothingOff()
        smoother.NonManifoldSmoothingOn()
        smoother.NormalizeCoordinatesOn()
        smoother.SetPassBand(0.08)
        smoother.Update()

        normals = vtk.vtkPolyDataNormals()
        normals.SetInputConnection(smoother.GetOutputPort())
        normals.ConsistencyOn()
        normals.SplittingOff()
        normals.AutoOrientNormalsOn()
        normals.Update()

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(normals.GetOutputPort())
        mapper.ScalarVisibilityOff()

        fill_opacity, edge_opacity, edge_width = self._overlay_style(block)

        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*color)
        actor.GetProperty().SetOpacity(fill_opacity)
        actor.GetProperty().SetSpecular(0.18)
        actor.GetProperty().SetSpecularPower(12)
        actor.GetProperty().SetDiffuse(0.92)
        actor.GetProperty().SetAmbient(0.42)
        actor.GetProperty().SetInterpolationToPhong()
        actor.GetProperty().BackfaceCullingOff()
        self.actors.append(actor)
        self.renderer.AddActor(actor)

        edge_actor = vtk.vtkActor()
        edge_actor.SetMapper(mapper)
        edge_actor.GetProperty().SetRepresentationToWireframe()
        edge_actor.GetProperty().SetColor(*ROI_EDGE_COLOR)
        edge_actor.GetProperty().SetOpacity(edge_opacity)
        edge_actor.GetProperty().SetLineWidth(edge_width)
        edge_actor.GetProperty().LightingOff()
        self.actors.append(edge_actor)
        self.renderer.AddActor(edge_actor)

    def _block_world_matrix(self, block: ROIBlock) -> vtk.vtkMatrix4x4:
        spacing = self.image_spacing or (1.0, 1.0, 1.0)
        origin = self.image_origin or (0.0, 0.0, 0.0)
        direction = np.array(self.image_direction, dtype=np.float64).reshape(3, 3)
        bbox_start = np.array(
            [block.bbox_x[0] * spacing[0], block.bbox_y[0] * spacing[1], block.bbox_z[0] * spacing[2]],
            dtype=np.float64,
        )
        translation = np.array(origin, dtype=np.float64) + direction @ bbox_start

        matrix = vtk.vtkMatrix4x4()
        for row in range(3):
            for col in range(3):
                matrix.SetElement(row, col, float(direction[row, col] * spacing[col]))
            matrix.SetElement(row, 3, float(translation[row]))
        matrix.SetElement(3, 0, 0.0)
        matrix.SetElement(3, 1, 0.0)
        matrix.SetElement(3, 2, 0.0)
        matrix.SetElement(3, 3, 1.0)
        return matrix

    def _overlay_style(self, block: ROIBlock) -> Tuple[float, float, float]:
        vol = float(block.volume_cm3)
        if vol < 20.0:
            return 0.62, 1.0, 2.4
        if vol < 120.0:
            return 0.50, 0.98, 2.0
        if vol < 400.0:
            return 0.42, 0.95, 1.8
        return 0.30, 0.90, 1.4
