import sys
from PIL import Image, ImageDraw, ImageFont
D=sys.argv[1]
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
iso=Image.open(f"{D}/dock_iso.png").convert("RGB")
iso=callouts(iso,[
 ((60,640),(522,446),"nose fairing (front)"),
 ((560,180),(752,348),"Bee35 coming in to land in pocket P2"),
 ((290,120),(621,380),"guide post, one per pocket"),
 ((30,300),(738,484),"empty pocket: seat, coffee-box well, 4 magnet pads"),
 ((40,760),(708,507),"short-range UWB beacon at each pocket (variant)"),
 ((1060,200),(998,273),"UWB anchor A1, starboard wingtip"),
 ((1300,470),(1257,543),"UWB anchor A3, fin"),
 ((1180,380),(1065,470),"mothership lift rotor"),
 ((1160,900),(1110,700),"Pioneer 3200 fuselage"),
 ((420,920),(800,533),"catch bracket on each drone"),
])
cl=Image.open(f"{D}/dock_close.png").convert("RGB")
cl=callouts(cl,[
 ((40,120),(352,275),"guide post, 0.28 m tall"),
 ((960,60),(740,265),"catch bracket: V mouth +-0.10 m, 12 mm slot"),
 ((40,700),(635,662),"pocket UWB beacon on a 5 cm stand"),
 ((860,400),(780,590),"coffee-box well, 0.13 m"),
 ((1180,860),(1265,700),"ridge between pockets, 38 mm"),
 ((60,420),(470,470),"parked Bee35 (props shown as discs)"),
])
W=1600; title_h=80
out=Image.new("RGB",(W,title_h+1000+1000+40),(255,255,255))
d=ImageDraw.Draw(out)
d.text((30,20),"Passive four-pocket dock on the Pioneer 3200 (simulator geometry, docksim)",font=F(36),fill=(10,10,10))
out.paste(iso,(0,title_h)); out.paste(cl,(0,title_h+1000+40))
d.text((30,title_h+1000+5),"Close-up from the port side: posts, brackets, beacons, pocket wells",font=F(26),fill=(60,60,60))
out.save(f"{D}/dock_picture.png"); print("ok", out.size)
