"""기존 챗봇에 연결하는 비상 검사기록표 출력 모듈."""
import io
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from flask import abort, send_file
from PIL import Image, ImageDraw, ImageFont
from emergency_record import EmergencyRecord, reply, reply

ROOT = Path(__file__).resolve().parent
BASE = (os.getenv('PUBLIC_BASE_URL') or os.getenv('RENDER_EXTERNAL_URL') or '').rstrip('/')
FONT = Path(os.getenv('KOREAN_FONT', str(ROOT/'emergency_ko.ttc')))
FIELDS = {
    '모델명':'DM-MR-GL(18)', '승강기 종류':'장애인용', '운행상태':'운행중',
    '정격속도':'1.5 m/s','최초 설치일':'2016년 04월 29일','제조업체':'(주)대오정공',
    '유지관리업체':'명성엘리베이터','하중':'1150kg','정원':'17명','운행구간':'1-6층',
    '카 내부 폭':'1800mm','카 내부 깊이':'1500mm','출입문 폭':'1000mm','출입문 높이':'2200mm'
}
ICONS = ['🏷️','🛗','🚦','💨','🏗️','🏭','🔧','📦','👨‍👩‍👧','↕️','📏','📏','🚪','🚪']
images = {}
images_lock = threading.Lock()


class IntegratedRecord(EmergencyRecord):
    def menu(self,s,n,r):
        values=r['specs']
        lines=[f'⚙️ [{r["label"]} 제원표 · {n}]','━━━━━━━━━━━━━━']
        for i,k in enumerate(FIELDS):
            if i==10:
                lines.extend(['','📐 [카 및 출입문 치수]','━━━━━━━━━━━━━━'])
            lines.append(f'{ICONS[i]} {k}: {values.get(k,"미입력")}')
        if '기계실 위치' in values:
            lines.append('기계실 위치: '+values['기계실 위치'])
        buttons=[self.button(s,n,label,action) for label,action in (
            ('측정값 입력하기','구역'),('제원 추가/수정','제원'),('기록표 출력','출력'))]+[('취소', '비상 기록표 취소')]
        return reply('\n'.join(lines)[:950],buttons)



def render(number, data, values):
    parsed=urlsplit(BASE)
    if parsed.scheme!='https' or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('HTTPS 공개 주소가 필요합니다')
    im=Image.open(ROOT/'form.jpg').convert('RGB')
    d=ImageDraw.Draw(im)
    def draw(x,y,value,size=12,left=False):
        d.text((x,y),str(value),font=ImageFont.truetype(str(FONT),size,index=1),anchor='lm' if left else 'mm',fill='#142438')
    draw(490,136,'테스트용 · 실제 검사기록 아님',14)
    for x,y,label in [(428,166,'건물명'),(880,193,'고유번호'),(206,219,'승강기 종류'),
        (428,219,'최초 설치일'),(652,219,'제조업체'),(880,219,'유지관리업체'),
        (206,247,'모델명'),(428,247,'운행구간'),(428,284,'정격속도'),(428,312,'하중'),
        (880,340,'정원'),(206,393,'카 내부 폭'),(428,393,'카 내부 깊이'),
        (652,420,'출입문 폭'),(880,420,'출입문 높이'),(206,532,'기계실 위치')]:
        draw(x,y,{'고유번호':number}.get(label,data.get(label,'')))
    draw(880,166,data.get('호기',''))
    for box,x,y,caption,key in [
        ((48,551,141,589),206,570,'균형추 완충기거리','counterweight_runby'),
        ((719,591,813,631),880,610,'카 완충기 거리','car_runby')]:
        d.rectangle(box,fill='white')
        c=(box[0]+box[2])//2
        d.text((c,y),caption,font=ImageFont.truetype(str(FONT),9,index=1),anchor='mm',fill='#142438')
        if key in values:
            draw(x,y,values[key]+' mm')
    d.rectangle((47,764,945,900),fill='white',outline='black')
    d.line((47,872,945,872),fill='black')
    draw(53,781,'특이사항:',13,True)
    notes='\n'.join(line for line in data.get('특이사항','').splitlines())
    font=ImageFont.truetype(str(FONT),13,index=1)
    chunks=[]
    for line in notes.splitlines():
        while line:
            k=len(line)
            while font.getlength(line[:k])>875:
                k-=1
            chunks.append(line[:k])
            line=line[k:]
    for i,line in enumerate(chunks[:4]):
        draw(53,798+i*17,line,13,True)
    if len(chunks)>4:
        draw(53,861,'※ 특이사항 계속: 아래 채팅 메시지 참조',11,True)
    draw(53,886,'유지관리업체명: '+data.get('유지관리업체',''),12,True)
    draw(420,886,'보험가입정보: '+data.get('보험사','조회 안 됨')+' · 만료일: '+data.get('보험 만료일','-'),12,True)
    for i,(p,label) in enumerate((('main','주 로프'),('car_gov','카측 과속조절기 로프'),('cw_gov','균형추측 과속조절기 로프'))):
        y=965+i*91
        d.rectangle((290,y+48,580,y+73),fill='white')
        draw(435,y+61,label+' 잔존 직경 비율')
        for j,k in enumerate(('worn','unworn','remaining_percent')):
            draw(801,y+j*30,values.get(p+'_'+k,''),14)
    for y,k in zip((1237,1267,1298,1327),('up_speed','up_current','down_speed','down_current')):
        v=values.get(k,'')
        if 'speed' in k and v:
            from decimal import Decimal
            v=f'{Decimal(v):.4f}'
        draw(801,y,v,14)
    stream=io.BytesIO()
    im.save(stream,format='PNG')
    token=secrets.token_urlsafe(24)
    now=time.monotonic()
    with images_lock:
        for k in [k for k,(_,expiry) in images.items() if expiry<=now]:
            del images[k]
        if len(images)>=64:
            del images[next(iter(images))]
        images[token]=(stream.getvalue(),now+3600)
    outputs=[{'simpleText':{'text':f'테스트용 비상 검사기록표 · {data.get("호기","")} ({number})'}},
             {'simpleImage':{'imageUrl':f'{BASE}/emergency-record/{token}.png','altText':'테스트용 비상 검사기록표'}}]
    if len(chunks)>4:
        outputs.extend({'simpleText':{'text':notes[i:i+475]}} for i in range(0,len(notes),475))
        # 카카오 출력 요소 수를 제한하고, 저장된 원문을 잘라 버리지 않는다.
        if len(outputs)>3:
            outputs=outputs[:2]+[{'simpleText':{'text':'특이사항이 길어 이미지에 일부만 표시되었습니다. 구역별 입력 결과에서 전체 내용을 확인해 주세요.'}}]
    return outputs


def record(token):
    with images_lock:
        entry=images.get(token)
        if not entry or entry[1]<=time.monotonic():
            abort(404)
    result=send_file(io.BytesIO(entry[0]),mimetype='image/png',download_name='emergency-record.png')
    result.headers['Cache-Control']='private, no-store'
    return result


def attach(app, list_units, get_specs):
    app.add_url_rule('/emergency-record/<token>.png','emergency_record_png',record)
    return IntegratedRecord(list_units,get_specs,(*FIELDS,'기계실 위치'),render)
