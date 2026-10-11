from PySide6.QtGui import QTransform

from geostitch.transforms import MatrixTransform


def to_qtransform(mapping: MatrixTransform) -> QTransform:
    coefficients = mapping.coefficients
    return QTransform(
        coefficients[0], coefficients[3], coefficients[6],
        coefficients[1], coefficients[4], coefficients[7],
        coefficients[2], coefficients[5], coefficients[8],
    )


def from_qtransform(transform: QTransform, model: str | None = None) -> MatrixTransform:
    return MatrixTransform((
        transform.m11(), transform.m21(), transform.m31(),
        transform.m12(), transform.m22(), transform.m32(),
        transform.m13(), transform.m23(), transform.m33(),
    ), model or ("affine" if transform.isAffine() else "projective"))