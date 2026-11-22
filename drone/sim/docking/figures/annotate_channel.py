import sys
from PIL import Image, ImageDraw, ImageFont
D=sys.argv[1]; OUT=sys.argv[2]
F=lambda s: ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", s)
def callouts(img, items, fs=24):
    d=ImageDraw.Draw(img)
    for (tx,ty),(px,py),text in items:
        f=F(fs); w,h=d.textbbox((0,0),text,font=f)[2:]
        d.line([(px,py),(tx+w/2,ty+h/2)],fill=(30,30,30),width=2)
        d.ellipse([px-5,py-5,px+5,py+5],fill=(30,30,30))
        d.rounded_rectangle([tx-8,ty-6,tx+w+8,ty+h+8],radius=6,fill=(255,255,255),outline=(60,60,60),width=2)
        d.text((tx,ty),text,font=f,fill=(20,20,20))
    return img
iso=Image.open(f"{D}/chan_iso.png").convert("RGB")
iso=callouts(iso,[
 ((1090,150),(1010,420),"funnel mouth at the rear, 0.50 m wide"),
 ((560,140),(930,390),"drone flying in nose first at 0.10 m"),
 ((60,330),(700,470),"lane walls 0.22 m: 10 mm clearance, 3 mm guide rail at the floor"),
 ((80,420),(718,450),"tall rear plate on each drone (meets the next one's nose)"),
 ((60,880),(600,540),"parked drones nose to tail on the floor magnets"),
 ((60,760),(540,580),"front stop"),
 ((1070,560),(1207,470),"twin fins (one fin blocks the mouth)"),
 ((1150,740),(1065,600),"mothership lift rotor"),
 ((1180,200),(1014,252),"UWB anchor A3, starboard fin"),
])
top=Image.open(f"{D}/chan_top.png").convert("RGB")
top=callouts(top,[
 ((60,110),(470,410),"funnel (0.40 m long)"),
 ((850,330),(820,445),"lane, slots 0.22 m apart"),
 ((640,600),(700,500),"empty slots: box wells"),
 ((1220,330),(1100,470),"front stop"),
 ((60,860),(196,730),"A3 on the starboard fin"),
 ((1180,860),(1040,770),"lift rotors"),
])
W=1600; th=80
out=Image.new("RGB",(W,th+1000+40+1000),(255,255,255))
d=ImageDraw.Draw(out)
d.text((30,20),"Channel dock variant: one lane, funnel mouth at the rear, drones nose to tail (docksim)",font=F(34),fill=(10,10,10))
out.paste(iso,(0,th)); out.paste(top,(0,th+1040))
d.text((30,th+1005),"Top view (nose to the right)",font=F(26),fill=(60,60,60))
out.save(OUT); print("ok")
