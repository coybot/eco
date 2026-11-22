import re, sys, numpy as np, mujoco
sys.path.insert(0, ".")
from docksim.config import load_cfg, effective_cfg
from docksim.scene import build_xml, DroneSpec
from PIL import Image
OUT=sys.argv[1]; views=sys.argv[2].split(",") if len(sys.argv)>2 else None
cfg=load_cfg(); cfg["channel_dock"]["enabled"]=True; cfg=effective_cfg(cfg); px=cfg["dock"]["pocket_x_m"]; ma=cfg["mothership_airframe"]
from docksim.config import channel_geometry; G=channel_geometry(cfg)
specs=[DroneSpec("a",2,(G["x_mouth"]+0.25,0.10,0.04),active=True),
       DroneSpec("n0",0,(px[0],0,0),box=True), DroneSpec("n1",1,(px[1],0,0))]
xml=build_xml(cfg,specs)
xml=re.sub(r'<freejoint[^>]*/>','',xml)
COL={"0":"0.96 0.94 0.89 1","1":"0.62 0.64 0.68 1","2":"0.16 0.17 0.19 1","3":"0.98 0.55 0.10 1","4":"0.55 0.36 0.22 1"}
def col(m):
    g=m.group(0); grp=re.search(r'group="(\d)"',g).group(1); name=re.search(r'name="([^"]+)"',g).group(1)
    c=COL[grp]
    if name.startswith("post"): c="0.85 0.12 0.12 1"
    elif name.startswith(("wall","funnel","stop")): c="0.45 0.55 0.70 0.85"
    elif "_bump" in name: c="0.98 0.55 0.10 1"
    elif name in ("lip","wedge_front","wedge_rear") or name.startswith("ridge"): c="0.45 0.55 0.70 1"
    elif "_pad" in name: c="0.75 0.75 0.78 1"
    elif name=="fairing": c="0.70 0.72 0.76 1"
    return g[:-2]+f' rgba="{c}"/>'
xml=re.sub(r'<geom [^>]*/>',col,xml)
extra=[]
pb=cfg["dock_sensors"]["pocket_beacons"]
for k,x in enumerate([]):
    extra.append(f'<geom name="pb{k}" type="sphere" contype="0" conaffinity="0" pos="{x} {pb["y_m"]} {pb["z_m"]}" size="0.012" rgba="0.1 0.8 0.3 1"/>')
    extra.append(f'<geom name="pbs{k}" type="cylinder" contype="0" conaffinity="0" fromto="{x} 0 {pb["z_m"]} {x} {pb["y_m"]} {pb["z_m"]}" size="0.004" rgba="0.3 0.3 0.3 1"/>')
for n in ("A1","A2","A3"):
    p=ma["anchors_m"][n]; extra.append(f'<geom name="anc{n}" type="sphere" contype="0" conaffinity="0" pos="{p[0]} {p[1]} {p[2]}" size="0.03" rgba="0.1 0.8 0.3 1"/>')
b=cfg["bee35"]; off=b["rotor_offset_m"]; rr=b["rotor_diameter_m"]/2
for sp in specs:
    x0,y0,z0=sp.pos_m
    for sx in (-1,1):
        for sz in (-1,1):
            extra.append(f'<geom type="cylinder" contype="0" conaffinity="0" fromto="{x0+sx*off} {y0+0.062} {z0+sz*off} {x0+sx*off} {y0+0.066} {z0+sz*off}" size="{rr}" rgba="0.95 0.95 0.95 0.55"/>')
ro=ma["rotors"]
for k,p in enumerate(ro["pos_m"]):
    extra.append(f'<geom name="rot{k}" type="cylinder" contype="0" conaffinity="0" fromto="{p[0]} {p[1]-0.004} {p[2]} {p[0]} {p[1]+0.004} {p[2]}" size="{ro["radius_m"]}" rgba="0.25 0.45 0.95 0.35"/>')
    extra.append(f'<geom name="hub{k}" type="cylinder" contype="0" conaffinity="0" fromto="{p[0]} {p[1]-0.06} {p[2]} {p[0]} {p[1]+0.01} {p[2]}" size="0.025" rgba="0.15 0.15 0.15 1"/>')
    # boom along x from the wing to the rotor
    extra.append(f'<geom name="boom{k}" type="capsule" contype="0" conaffinity="0" fromto="0.1 {p[1]-0.07} {p[2]} {p[0]} {p[1]-0.07} {p[2]}" size="0.015" rgba="0.55 0.57 0.6 1"/>')
body_open='<worldbody>\n<light directional="true" pos="0 0 3" dir="0 0 -1" diffuse="0.7 0.7 0.7"/><light directional="true" pos="2 -2 2" dir="-1 1 -1" diffuse="0.35 0.35 0.35"/>\n<body name="root" euler="90 0 0">'
xml=xml.replace('<worldbody>',body_open+"".join(extra),1).replace('</worldbody>','</body></worldbody>')
xml=xml.replace('<size memory="8M"/>','<size memory="8M"/><asset><texture name="sky" type="skybox" builtin="gradient" rgb1="0.80 0.88 0.97" rgb2="0.97 0.98 1.0" width="512" height="512"/></asset><visual><global offwidth="1600" offheight="1000"/><headlight ambient="0.45 0.45 0.45" diffuse="0.35 0.35 0.35"/><quality shadowsize="4096"/></visual>')
xml=xml.replace('gravity="0 -9.80665 0"','gravity="0 0 0"')
model=mujoco.MjModel.from_xml_string(xml); data=mujoco.MjData(model); mujoco.mj_forward(model,data)
r=mujoco.Renderer(model,1000,1600); cam=mujoco.MjvCamera(); opt=mujoco.MjvOption(); opt.geomgroup[:]=1
V={"iso":([-0.1,0.0,0.0],3.0,-120,-35),"close":([-0.25,0.0,0.08],1.25,-135,-28),"top":([0,0,0],2.4,90,-89.9),"wide":([-0.1,0,0],5.2,-45,-30),"front":([0,0,0.05],1.6,0,-8)}
for k,(la,d,az,el) in V.items():
    if views and k not in views: continue
    cam.lookat[:]=la; cam.distance=d; cam.azimuth=az; cam.elevation=el
    r.update_scene(data,camera=cam,scene_option=opt); Image.fromarray(r.render()).save(f"{OUT}/dock_{k}.png"); print("saved",k)
