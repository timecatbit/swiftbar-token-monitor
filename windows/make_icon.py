#!/usr/bin/env python3
"""Write the tray star as the program icon: one ICO with a PNG for each size Windows asks for."""
import struct
import sys

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtWidgets import QApplication

from panel import star_pixmap

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def png(size):
    buffer=QBuffer();buffer.open(QIODevice.WriteOnly)
    if not star_pixmap(size).save(buffer,'PNG'):raise OSError('could not draw the icon')
    return bytes(buffer.data())


def icon_bytes():
    images=[png(size) for size in SIZES]
    head=struct.pack('<HHH',0,1,len(images));offset=len(head)+16*len(images);entries=b''
    for size,data in zip(SIZES,images):
        # 0 in the width and height bytes means 256.
        entries+=struct.pack('<BBBBHHII',size%256,size%256,0,0,1,32,len(data),offset);offset+=len(data)
    return head+entries+b''.join(images)


def main(path):
    # Drawing a pixmap needs a GUI application.
    application=QApplication.instance() or QApplication(sys.argv[:1])
    data=icon_bytes()
    with open(path,'wb') as handle:handle.write(data)
    application.quit()
    return 0


if __name__=='__main__':
    sys.exit(main(sys.argv[1]))
