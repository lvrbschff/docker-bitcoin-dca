import ast
import sys
import html
from html.parser import HTMLParser
from pathlib import Path
import re
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/'web_ui.py').read_text()
module=ast.parse(source)
page=next(ast.literal_eval(n.value) for n in module.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='HTML' for t in n.targets))

class Node:
 def __init__(self,tag,attrs=()):self.tag=tag;self.attrs=dict(attrs);self.children=[]
 def text(self):return ' '.join(' '.join(c.text() if isinstance(c,Node) else c for c in self.children).split())
 def find(self,predicate):
  out=[]
  for c in self.children:
   if isinstance(c,Node):
    if predicate(c):out.append(c)
    out.extend(c.find(predicate))
  return out
 def cls(self,c):return c in self.attrs.get('class','').split()
class Parser(HTMLParser):
 def __init__(self):super().__init__();self.root=Node('root');self.stack=[self.root]
 def handle_starttag(self,tag,attrs):
  node=Node(tag,attrs);self.stack[-1].children.append(node)
  if tag not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:self.stack.append(node)
 def handle_endtag(self,tag):
  if len(self.stack)>1:self.stack.pop()
 def handle_data(self,data):self.stack[-1].children.append(data)
p=Parser();p.feed(page)
main=p.root.find(lambda n:n.tag=='main')[0]
by_id={n.attrs['id']:n for n in main.find(lambda n:'id' in n.attrs)}
sections=[n for n in main.children if isinstance(n,Node) and n.tag=='section']
assert len(sections)==4
portfolio,configuration,schedule,report=sections

BG='#0b1020';PANEL='#121a2d';LINE='#26324a';TEXT='#edf2ff';MUTED='#9ba9c4';ACCENT='#8bafff';GREEN='#61d7ae';CARD='#0c1425'
image=Image.new('RGB',(1440,3400),BG);draw=ImageDraw.Draw(image)
parts=[]
fontdir=Path('/System/Library/Fonts/Supplemental')
def font(size,bold=False):
 try:return ImageFont.truetype(str(fontdir/('Arial Bold.ttf' if bold else 'Arial.ttf')),size)
 except OSError:
  try:return ImageFont.truetype('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf',size)
  except OSError:return ImageFont.load_default(size=size)
def rect(x,y,w,h,fill=PANEL,stroke=LINE,r=14):
 parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}"'+(f' stroke="{stroke}"' if stroke else '')+'/>')
 draw.rounded_rectangle((x,y,x+w,y+h),r,fill=fill,outline=stroke,width=1)
def line(x,y,x2,y2):
 parts.append(f'<path d="M{x} {y}H{x2}" stroke="{LINE}"/>');draw.line((x,y,x2,y2),fill=LINE)
def text(x,y,value,size=15,color=TEXT,bold=False):
 value=str(value)
 if value=='⌄':
  parts.append(f'<path d="M{x} {y-5}l4 4 4-4" fill="none" stroke="{color}" stroke-width="1.5"/>')
  draw.line([(x,y-5),(x+4,y-1),(x+8,y-5)],fill=color,width=1);return
 parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}"'+(' font-weight="700"' if bold else '')+'>'+html.escape(value)+'</text>')
 draw.text((x,y),value,font=font(size,bold),fill=color,anchor='ls')
def wrap(value,width,size=12,bold=False):
 result=[];line_=''
 for word in value.split():
  test=(line_+' '+word).strip()
  if draw.textlength(test,font=font(size,bold))>width and line_:result.append(line_);line_=word
  else:line_=test
 if line_:result.append(line_)
 return result

def paragraph(x,y,value,width,size=12,color=MUTED,leading=20):
 for row in wrap(value,width,size):text(x,y,row,size,color);y+=leading
 return y

def button(x,y,w,label,primary=False):
 rect(x,y,w,45,ACCENT if primary else CARD,ACCENT if primary else LINE,10)
 width=draw.textlength(label,font=font(15,primary));text(x+(w-width)/2,y+28,label,15,'#081122' if primary else TEXT,primary)
def heading(x,y,label,reference=None):
 text(x,y,label,17,TEXT,True)
 if reference is not None:text(x+draw.textlength(label,font=font(17,True))+5,y-5,str(reference),11,ACCENT,True)

values={'balance':'0.001 BTC','spot':'€84,000.00/BTC','spotUpdated':'Kraken public price page · fetched 12:00:00',
'balanceValue':'€84.00','settingExchange':'Kraken','settingQuoteCurrency':'EUR','tradingMode':'Live',
'buyAmount':'Exchange minimum','depositDay':'1','withdrawEnabled':'Enabled','threshold':'€1,000.00','thresholdNote':'Threshold in EUR, shown in EUR',
'nextPurchaseAt':'28/10/2026, 14:34:56 CET','nextPurchaseStatus':'Scheduled','nextPurchaseCountdown':'02h 34m 56s',
'buyCount':'4','buyBtc':'0.0055 BTC','buyVolume':'€440.00','avgPrice':'Average: €80,000.00/BTC','tradeFees':'€1.10',
'withdrawBtc':'0.0044 BTC','withdrawCount':'1 confirmed · 0 pending','profitLoss':'€20.90',
'withdrawFees':'€8.10','netBtc':'0.001 BTC','updated':'12:00:00'}

def grid(node,x,y,width,cols,height):
 gap=14;cw=(width-gap*(cols-1))/cols
 cards=[n for n in node.children if isinstance(n,Node) and n.cls('card')]
 for i,card in enumerate(cards):
  cx=x+(i%cols)*(cw+gap);cy=y+(i//cols)*(height+gap)
  rect(round(cx,2),cy,round(cw,2),height,CARD,LINE,14)
  label=card.find(lambda n:n.tag=='label')[0].text()
  label_lines=wrap(label,cw-34,12)
  baseline=cy+28
  for row in label_lines:text(cx+17,baseline,row,12,MUTED);baseline+=17
  value_node=card.find(lambda n:n.cls('value'))[0]
  identifier=value_node.attrs.get('id')
  if identifier=='periodLabel':
   text(cx+17,cy+62,'From: 20/10/2026',16,TEXT,True);text(cx+17,cy+84,'00:00:00',16,TEXT,True)
   text(cx+17,cy+106,'To: 28/10/2026',16,TEXT,True);text(cx+17,cy+128,'12:00:00',16,TEXT,True)
  else:
   value=values.get(identifier,value_node.text());size=22
   if value_node.cls('time-value'):size=16
   if value_node.cls('countdown-value'):size=20
   # CSS wraps long values; use the same width boundary for the illustration.
   value_y=baseline+15
   for row in wrap(value,cw-34,size,True):text(cx+17,value_y,row,size,GREEN if identifier=='profitLoss' else TEXT,True);value_y+=29
   value_y-=9
   for small in card.find(lambda n:n.cls('small')):
    for row in wrap(values.get(small.attrs.get('id'),small.text()),cw-34,12):text(cx+17,value_y,row,12,MUTED);value_y+=18
 return y+((len(cards)+cols-1)//cols)*(height+gap)-gap

X=152;W=1136;CX=X+20;CW=W-40
text(X,112,'Bitcoin DCA Bot',30,TEXT,True)
text(X,141,'Portfolio overview, bot configuration and activity analysis, with optional Parqet CSV export',15,MUTED)
button(X+W-93,36,93,'Log out')
button(X+W-238,91,238,'Withdraw BTC manually',False);text(X+W-20,106,'4',11,TEXT)
text(X+W-170,164,'Display currency',12,MUTED)
rect(X+W-170,174,170,45,CARD,LINE,10);text(X+W-157,202,'Euro (EUR)',15);text(X+W-21,202,'⌄',15,MUTED)

Y=245
rect(X,Y,W,186);heading(CX,Y+33,'Bot-tracked portfolio',1)
grid(portfolio.find(lambda n:n.cls('grid'))[0],CX,Y+55,CW,4,108)
Y+=204
rect(X,Y,W,298);heading(CX,Y+34,'Bot configuration')
grid(configuration.find(lambda n:n.cls('grid'))[0],CX,Y+55,CW,4,104)
Y+=316
rect(X,Y,W,180);heading(CX,Y+34,'Purchase schedule',2)
grid(schedule.find(lambda n:n.cls('grid'))[0],CX,Y+55,CW,2,102)
Y+=218
line(X,Y,X+W,Y);text(X,Y+48,'Period analysis',21,TEXT,True);text(X+draw.textlength('Period analysis',font=font(21,True))+5,Y+39,'3',11,ACCENT,True)
text(X,Y+75,'Choose a date range to review activity and prepare a Parqet export.',14,MUTED)
Y+=100
rect(X,Y,W,119)
# Three equal input columns plus report action match the desktop controls.
for x,w,label,value in [(CX,265,'Withdrawal interval','Custom date range'),(CX+277,265,'From','20/10/2026, 00:00'),(CX+554,265,'To','28/10/2026, 12:00')]:
 text(x,Y+32,label,12,MUTED);rect(x,Y+42,w,45,CARD,LINE,10);text(x+12,Y+71,value,15)
text(CX+239,Y+69,'⌄',15,MUTED)
button(CX+831,Y+42,265,'Load report',True)
Y+=137
summary=report.find(lambda n:n.tag=='section' and n.cls('panel'))[1]
rect(X,Y,W,405);heading(CX,Y+33,'Selected period summary')
grids=summary.find(lambda n:n.cls('grid'))
grid(grids[0],CX,Y+55,CW,5,166)
grid(grids[1],CX,Y+235,CW,4,148)
Y+=423
rect(X,Y,W,390);heading(CX,Y+34,'Activity')
text(X+W-238,Y+33,'Entries per page',12,MUTED);rect(X+W-104,Y+12,84,36,CARD,LINE,9);text(X+W-87,Y+35,'20',13);text(X+W-41,Y+35,'⌄',13,MUTED)
columns=[CX+10,CX+238,CX+410,CX+553,CX+731,CX+870,CX+986]
for x,label in zip(columns,['DATE','TYPE','BTC','PRICE','VALUE','FEE','ACTION']):text(x,Y+78,label,11,MUTED,True)
line(CX,Y+91,CX+CW,Y+91)
rows=[
['27/10/2026, 11:00:00','Buy','0.001 BTC','€81,000.00/BTC','€81.00','€0.20','Delete'],
['23/10/2026, 14:00:00','Withdrawal · completed','0.0044 BTC','€81,000.00/BTC','€356.40','€8.10',''],
['22/10/2026, 12:00:00','Buy','0.002 BTC','€78,000.00/BTC','€156.00','€0.39','Delete'],
['21/10/2026, 12:00:00','Buy','0.0015 BTC','€82,000.00/BTC','€123.00','€0.31','Delete'],
['20/10/2026, 12:00:00','Buy','0.001 BTC','€80,000.00/BTC','€80.00','€0.20','Delete']]
for i,row in enumerate(rows):
 ry=Y+123+i*43
 for x,value in zip(columns,row):
  if value=='Delete':rect(x-4,ry-21,72,31,CARD,LINE,8);text(x+7,ry,'Delete',13)
  else:text(x,ry,value,13)
 line(CX,ry+13,CX+CW,ry+13)
button(CX,Y+332,99,'Previous');text(CX+CW/2-125,Y+360,'Showing 1–5 of 5 · Page 1 of 1',13,MUTED);button(CX+CW-74,Y+332,74,'Next')
Y+=408
button(X+W-209,Y,209,'Download Parqet CSV')
Y+=75
line(X,Y,X+W,Y);heading(X,Y+34,'Notes')
Y+=65
notes=main.find(lambda n:n.tag=='li')
for index,note in enumerate(notes,1):
 strong=note.find(lambda n:n.tag=='strong')[0].text()
 text(X,Y,f'{index}.',12,MUTED);text(X+21,Y,strong,12,TEXT,True);Y+=20
 for para in note.find(lambda n:n.tag=='p'):
  value=para.text()
  if para.attrs.get('id')=='withdrawalDestination':value='Kraken saved withdrawal method: Example Bitcoin wallet (fictional)'
  if para.attrs.get('id')=='withdrawMessage':value='Manual withdrawals are available. Automatic withdrawals may remain disabled.'
  Y=paragraph(X+21,Y,value,W-21,12,MUTED,20)
 Y+=14
H=Y+30
svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="{H}" viewBox="0 0 1440 {H}" role="img" aria-labelledby="title desc">
<title id="title">Bitcoin DCA Bot — current web UI illustration</title>
<desc id="desc">Illustration of the current dashboard using fictional data: Bitcoin portfolio, manual withdrawal below Log out, bot configuration, purchase schedule, period analysis, activity table, Parqet export and configuration footnotes. This is not a live account screenshot.</desc>
<style>text{{font-family:Arial,Helvetica,sans-serif}}</style>
<rect width="1440" height="{H}" fill="{BG}"/>
'''+''.join(parts)+'\n</svg>\n'
(ROOT/'docs/web-ui-example-current.svg').write_text(svg)
if '--png' in sys.argv:image.crop((0,0,1440,H)).save(ROOT/'docs/web-ui-example-current.png')
print(f'Generated preview: 1440 × {H}; {len(notes)} current notes, {len(rows)} fictional activities')
