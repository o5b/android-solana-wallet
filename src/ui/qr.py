"""QR-code rendering helper for the ``ui/`` package.

A tiny pure helper used by the balance / address-page module (the Wallet Info
dialog and the "Show QR Code" / inline QR on the address page). Lives in its
own module so any future ``ui/`` consumer can render a QR without pulling in
the much larger balance module.

Returns a base64-encoded PNG (suitable for direct use as ``flet.Image.src``).
The QR itself is rendered with ``qrcode`` + ``Pillow``; nothing here depends
on ``flet`` or on the live ``page``.
"""

import base64
import io

import qrcode


def generate_qr_base64(data: str, box_size: int = 8, border: int = 2) -> str:
    """Render ``data`` as a base64-encoded PNG QR code.

    Parameters
    ----------
    data:
        The string to encode (typically a wallet address).
    box_size / border:
        ``qrcode`` render parameters (pixels per module / quiet-zone width).
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()
