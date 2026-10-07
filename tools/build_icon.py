"""Render the editable vector master into Windows icon sizes."""
from pathlib import Path
from io import BytesIO
from PySide6.QtCore import Qt,QBuffer,QIODevice
from PySide6.QtGui import QImage,QPainter
from PySide6.QtSvg import QSvgRenderer
from PIL import Image

root=Path(__file__).resolve().parents[1]
assets=root/'mapdln'/'assets'
renderer=QSvgRenderer(str(assets/'app.svg'))
assert renderer.isValid()
image=QImage(1024,1024,QImage.Format.Format_ARGB32_Premultiplied)
image.fill(Qt.GlobalColor.transparent)
painter=QPainter(image);renderer.render(painter);painter.end()
buf=QBuffer();buf.open(QIODevice.OpenModeFlag.WriteOnly);image.save(buf,'PNG')
master=Image.open(BytesIO(bytes(buf.data())))
master.resize((512,512),Image.Resampling.LANCZOS).save(assets/'app.png')
master.save(assets/'app.ico',sizes=[(n,n) for n in (16,20,24,32,40,48,64,128,256)])
print('Rendered app.png and multi-size app.ico from app.svg')
