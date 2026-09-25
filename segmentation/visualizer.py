from __future__ import annotations

import numpy as np
import vtk
from vtkmodules.util import numpy_support


class SegmentationVisualizer:
    _SURFACE_COLOR = (1.0, 0.25, 0.20)
    _SURFACE_OPACITY = 0.45

    def __init__(self, renderer: vtk.vtkRenderer):
        self._renderer = renderer
        self._surface_actor = None
        self._volume_actor = None
        self._centerline_actor = None
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active and self._surface_actor is not None

    def add_vessel_surface(
        self,
        mask: np.ndarray,
        spacing: tuple,
        origin: tuple,
        color: tuple = None,
        opacity: float = None,
    ):
        self.remove_all()
        color = color or self._SURFACE_COLOR
        opacity = opacity or self._SURFACE_OPACITY

        vtk_image = vtk.vtkImageData()
        shape = mask.shape
        vtk_image.SetDimensions(shape[2], shape[1], shape[0])
        vtk_image.SetSpacing(spacing)
        vtk_image.SetOrigin(origin)

        flat = mask.ravel(order="C").astype(np.uint8)
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

        smoother = vtk.vtkWindowedSincPolyDataFilter()
        smoother.SetInputConnection(contour.GetOutputPort())
        smoother.SetNumberOfIterations(10)
        smoother.BoundarySmoothingOff()
        smoother.NonManifoldSmoothingOn()
        smoother.SetPassBand(0.05)
        smoother.Update()

        normals = vtk.vtkPolyDataNormals()
        normals.SetInputConnection(smoother.GetOutputPort())
        normals.ConsistencyOn()
        normals.SplittingOff()
        normals.Update()

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(normals.GetOutputPort())
        mapper.ScalarVisibilityOff()

        self._surface_actor = vtk.vtkActor()
        self._surface_actor.SetMapper(mapper)
        self._surface_actor.GetProperty().SetColor(color)
        self._surface_actor.GetProperty().SetOpacity(opacity)
        self._surface_actor.GetProperty().SetSpecular(0.3)
        self._surface_actor.GetProperty().SetSpecularPower(20)
        self._surface_actor.GetProperty().SetDiffuse(0.8)
        self._surface_actor.GetProperty().SetAmbient(0.2)

        self._renderer.AddActor(self._surface_actor)
        self._active = True

    def add_vessel_volume(
        self,
        mask: np.ndarray,
        spacing: tuple,
        origin: tuple,
        color: tuple = None,
        opacity: float = 0.25,
    ):
        color = color or self._SURFACE_COLOR

        vtk_image = vtk.vtkImageData()
        shape = mask.shape
        vtk_image.SetDimensions(shape[2], shape[1], shape[0])
        vtk_image.SetSpacing(spacing)
        vtk_image.SetOrigin(origin)

        flat = (mask.astype(np.float32) * 255).ravel(order="C")
        vtk_array = numpy_support.numpy_to_vtk(flat, deep=True, array_type=vtk.VTK_UNSIGNED_CHAR)
        vtk_image.GetPointData().SetScalars(vtk_array)

        opacity_tf = vtk.vtkPiecewiseFunction()
        opacity_tf.AddPoint(0, 0.0)
        opacity_tf.AddPoint(1, opacity)
        opacity_tf.AddPoint(255, opacity)

        color_tf = vtk.vtkColorTransferFunction()
        color_tf.AddRGBPoint(0, 0, 0, 0)
        color_tf.AddRGBPoint(1, *color)
        color_tf.AddRGBPoint(255, *color)

        volume_prop = vtk.vtkVolumeProperty()
        volume_prop.SetScalarOpacity(opacity_tf)
        volume_prop.SetColor(color_tf)
        volume_prop.ShadeOn()
        volume_prop.SetInterpolationTypeToLinear()

        mapper = vtk.vtkGPUVolumeRayCastMapper()
        mapper.SetInputData(vtk_image)
        mapper.SetBlendModeToComposite()

        self._volume_actor = vtk.vtkVolume()
        self._volume_actor.SetMapper(mapper)
        self._volume_actor.SetProperty(volume_prop)
        self._renderer.AddVolume(self._volume_actor)

    def add_centerline(
        self,
        centerline: np.ndarray,
        spacing: tuple,
        origin: tuple,
        color: tuple = (1.0, 1.0, 0.0),
        radius: float = 0.2,
    ):
        from skimage.measure import marching_cubes
        verts, faces, _, _ = marching_cubes(centerline.astype(np.float32), level=0.5, spacing=spacing)

        verts_vtk = numpy_support.numpy_to_vtk(verts, deep=True, array_type=vtk.VTK_FLOAT)
        pts = vtk.vtkPoints()
        pts.SetData(verts_vtk)

        tris = vtk.vtkCellArray()
        for tri in faces:
            tris.InsertNextCell(3)
            tris.InsertCellPoint(int(tri[0]))
            tris.InsertCellPoint(int(tri[1]))
            tris.InsertCellPoint(int(tri[2]))

        poly = vtk.vtkPolyData()
        poly.SetPoints(pts)
        poly.SetPolys(tris)

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(poly)

        self._centerline_actor = vtk.vtkActor()
        self._centerline_actor.SetMapper(mapper)
        self._centerline_actor.GetProperty().SetColor(color)
        self._renderer.AddActor(self._centerline_actor)

    def remove_surface(self):
        if self._surface_actor and self._renderer.HasViewProp(self._surface_actor):
            self._renderer.RemoveActor(self._surface_actor)
        self._surface_actor = None
        self._active = False

    def remove_volume(self):
        if self._volume_actor and self._renderer.HasViewProp(self._volume_actor):
            self._renderer.RemoveVolume(self._volume_actor)
        self._volume_actor = None

    def remove_centerline(self):
        if self._centerline_actor and self._renderer.HasViewProp(self._centerline_actor):
            self._renderer.RemoveActor(self._centerline_actor)
        self._centerline_actor = None

    def remove_all(self):
        self.remove_surface()
        self.remove_volume()
        self.remove_centerline()
