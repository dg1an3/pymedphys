from typing import Tuple
from typing_extensions import Literal

import numpy as np
import pydicom
import xarray as xr

from pymedphys._dicom import dose


def xdose_from_dataset(
    ds: "pydicom.dataset.Dataset",
    name="Dose",
    coord_system: Literal["D", "P", "S"] = "S",
) -> "xr.DataArray":

    return xr.DataArray(
        data=dose.dose_from_dataset(ds),
        dims=xarray_dims_from_dataset(ds, coord_system=coord_system),
        coords=xarray_coords_from_dataset(
            ds,
            coord_system=coord_system,
        ),
        name=name,
        attrs={"units": ds.DoseUnits.title(), "coord_system": coord_system},
    )


def round_xdose_coords(xdose_to_round, decimals=2):
    xdose_rounded = xdose_to_round.copy()

    for dim, coord_vals in xdose_to_round.coords.items():
        xdose_rounded.coords[dim] = coord_vals.round(decimals=decimals)

    return xdose_rounded


def coords_from_dataset(
    ds: "pydicom.dataset.Dataset", coord_system: Literal["D", "P", "S"] = "S"
) -> Tuple["np.ndarray", "np.ndarray", "np.ndarray"]:
    r"""Returns the x, y and z coordinates of a DICOM dataset's
    pixel array in the specified coordinate system.

    For DICOM RT Dose datasets, these are the x, y, z coordinates of the
    dose grid.

    Parameters
    ----------
    ds : pydicom.dataset.Dataset
        A DICOM dataset that contains pixel data. Supported modalities
        include 'CT' and 'RTDOSE'.

    coord_system : str, optional
        The coordinate system in which to return the `x`, `y` and `z`
        coordinates of the DICOM dataset. The accepted, case-insensitive
        values of `coord_system` are:

        'D':
            Return coordinates in the DICOM coordinate system.

        'P':
            Return coordinates in the IEC patient coordinate system.

        'S':
            Return coordinates in the IEC support coordinate system.

    Returns
    -------
    (x, y, z)
        A tuple containing three `numpy.ndarray`s corresponding to the
        `x`, `y` and `z` coordinates of the DICOM dataset's pixel array
        in the specified coordinate system.

    Notes
    -----
    Supported scan orientations [1]_:

    =========================== ==========================
    Orientation                 ds.ImageOrientationPatient
    =========================== ==========================
    Feet First Decubitus Left   [ 0,  1,  0,  1,  0,  0]
    Feet First Decubitus Right  [ 0, -1,  0, -1,  0,  0]
    Feet First Prone            [ 1,  0,  0,  0, -1,  0]
    Feet First Supine           [-1,  0,  0,  0,  1,  0]
    Head First Decubitus Left   [ 0, -1,  0,  1,  0,  0]
    Head First Decubitus Right  [ 0,  1,  0, -1,  0,  0]
    Head First Prone            [-1,  0,  0,  0, -1,  0]
    Head First Supine           [ 1,  0,  0,  0,  1,  0]
    =========================== ==========================

    References
    ----------
    .. [1] O. McNoleg, "Generalized coordinate transformations for Monte
       Carlo (DOSXYZnrc and VMC++) verifications of DICOM compatible
       radiotherapy treatment plans", arXiv:1406.0014, Table 1,
       https://arxiv.org/ftp/arxiv/papers/1406/1406.0014.pdf
    """

    _validate_coord_system(coord_system)

    if not (
        np.allclose(np.abs(ds.ImageOrientationPatient), np.array([1, 0, 0, 0, 1, 0]))
        or np.allclose(np.abs(ds.ImageOrientationPatient), np.array([0, 1, 0, 1, 0, 0]))
    ):
        raise ValueError(
            "Dose grid orientation is not supported. Dose "
            "grid slices must be aligned along the "
            "superoinferior axis of patient."
        )

    is_decubitus = dataset_orientation_is_decubitus(ds)
    is_head_first = dataset_orientation_is_head_first(ds)

    di = float(ds.PixelSpacing[0])
    dj = float(ds.PixelSpacing[1])

    col_range = np.arange(0, ds.Columns * di, di)
    row_range = np.arange(0, ds.Rows * dj, dj)

    if is_decubitus:
        x_support = (
            ds.ImageOrientationPatient[1] * ds.ImagePositionPatient[1] + col_range
        )
        z_support = -(
            ds.ImageOrientationPatient[3] * ds.ImagePositionPatient[0] + row_range
        )
    else:
        x_support = (
            ds.ImageOrientationPatient[0] * ds.ImagePositionPatient[0] + col_range
        )
        z_support = -(
            ds.ImageOrientationPatient[4] * ds.ImagePositionPatient[1] + row_range
        )

    if is_head_first:
        y_support = ds.ImagePositionPatient[2] + np.array(ds.GridFrameOffsetVector)
    else:
        y_support = -ds.ImagePositionPatient[2] + np.array(ds.GridFrameOffsetVector)

    if coord_system.upper() == "S":
        return (x_support, y_support, z_support)

    elif coord_system.upper() in ("D", "P"):

        x_patient = (
            ds.ImageOrientationPatient[0] * x_support
            + ds.ImageOrientationPatient[3] * z_support
        )
        z_patient = (
            ds.ImageOrientationPatient[1] * x_support
            + ds.ImageOrientationPatient[4] * z_support
        )

        if not is_head_first:
            y_patient = -y_support
        else:
            y_patient = y_support

        if coord_system.upper() == "P":
            return (x_patient, y_patient, z_patient)

        elif coord_system.upper() == "D":

            x_dicom = x_patient
            y_dicom = -z_patient
            z_dicom = y_patient

            return (x_dicom, y_dicom, z_dicom)


def xarray_dims_from_dataset(
    ds: "pydicom.dataset.Dataset", coord_system: Literal["D", "P", "S"] = "S"
):
    _validate_coord_system(coord_system)

    # fmt: off
    XARRAY_DIMS_BY_COORD_SYSTEM_AND_ORIENT = {
        "S":     ["y", "z", "x"],
        "P":     ["y", "z", "x"],
        "P dec": ["y", "x", "z"],
        "D":     ["z", "y", "x"],
        "D dec": ["z", "x", "y"],
    }
    # fmt: on

    if dataset_orientation_is_decubitus(ds) and coord_system.upper() != "S":
        dim_key = f"{coord_system.upper()} dec"
    else:
        dim_key = coord_system.upper()

    return XARRAY_DIMS_BY_COORD_SYSTEM_AND_ORIENT[dim_key]


def xarray_coords_from_dataset(
    ds: "pydicom.dataset.Dataset",
    coord_system: Literal["D", "P", "S"] = "S",
):
    x, y, z = coords_from_dataset(ds, coord_system=coord_system)

    return {"x": x, "y": y, "z": z}


def dataset_orientation_is_head_first(ds: "pydicom.dataset.Dataset"):
    if dataset_orientation_is_decubitus(ds):
        return np.abs(np.sum(ds.ImageOrientationPatient)) != 2
    else:
        return np.abs(np.sum(ds.ImageOrientationPatient)) == 2


def dataset_orientation_is_decubitus(ds: "pydicom.dataset.Dataset"):
    return np.allclose(np.abs(ds.ImageOrientationPatient), np.array([0, 1, 0, 1, 0, 0]))


def _validate_coord_system(coord_system):

    VALID_COORD_SYSTEMS = ("D", "P", "S")

    if coord_system.upper() not in VALID_COORD_SYSTEMS:
        raise ValueError(
            "The supplied coordinate system is invalid. Valid systems are:\n{}".format(
                "\n".join(VALID_COORD_SYSTEMS)
            )
        )
