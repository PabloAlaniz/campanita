"""Lo que comparten los canales para armar texto plano."""

from __future__ import annotations

import re

#: Los caracteres de control, que no pueden ir en una cabecera ni en un texto plano.
CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
