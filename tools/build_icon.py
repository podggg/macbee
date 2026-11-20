#!/usr/bin/env python3
"""Render original SVG through AppKit into a native .icns; no screen access."""
import pathlib,subprocess,tempfile
ROOT=pathlib.Path(__file__).resolve().parents[1]

def build_icon(destination):
    destination=pathlib.Path(destination)
    with tempfile.TemporaryDirectory(prefix='macbee-icon-') as temp:
        temp=pathlib.Path(temp);binary=temp/'render-icon';iconset=temp/'Macbee.iconset';iconset.mkdir()
        subprocess.run(['xcrun','clang','-fobjc-arc','-fno-modules','-O2','-Wall','-Wextra','-Werror',
            '-framework','Cocoa',str(ROOT/'tools/render_icon.m'),'-o',str(binary)],check=True,timeout=30)
        for base in (16,32,128,256,512):
            for scale in (1,2):
                name='icon_%dx%d%s.png'%(base,base,'@2x' if scale==2 else '')
                subprocess.run([str(binary),str(ROOT/'Assets/Macbee.svg'),str(iconset/name),str(base*scale)],check=True,timeout=10)
        destination.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['iconutil','-c','icns',str(iconset),'-o',str(destination)],check=True,timeout=30)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('destination',type=pathlib.Path);args=parser.parse_args();build_icon(args.destination)
