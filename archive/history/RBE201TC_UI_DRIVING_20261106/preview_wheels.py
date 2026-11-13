from pathlib import Path
import math
out=Path(__file__).parent/'wheel-preview'
# Fixed wheel plane: orthogonal projected principal axes, calibrated to the hub.
wheels=[('front',596.5,498.5,.65,math.radians(16),54),('rear',998.5,333.5,.61,math.radians(14),48)]
def wheel(name,x,y,ratio,tilt,r,angle):
 a=ratio*math.cos(tilt);b=ratio*math.sin(tilt);c=-math.sin(tilt);d=math.cos(tilt);det=a*d-b*c
 inv=(d/det,-b/det,-c/det,a/det,(c*y-d*x)/det,(b*x-a*y)/det)
 return f'<defs><clipPath id="{name}{angle}"><circle r="{r}"/></clipPath></defs><g transform="matrix({a} {b} {c} {d} {x} {y})"><g clip-path="url(#{name}{angle})"><g transform="rotate({angle})"><image href="photo.jpg" width="1320" height="643" transform="matrix({" ".join(map(str,inv))})"/></g></g></g>'
html=['<html><body style="margin:12px;background:#000;color:#aaa;font:13px sans-serif;display:grid;grid-template-columns:repeat(4,1fr);gap:10px">']
for w in wheels:
 for angle in [0,90,180,270]:
  name,x,y,ratio,t,r=w
  html.append(f'<div>{name} {angle}°<svg width="100%" viewBox="{x-65} {y-85} 130 170"><image href="photo.jpg" width="1320" height="643"/>{wheel(*w,angle)}</svg></div>')
html.append('</body></html>')
(out/'index.html').write_text(''.join(html))
