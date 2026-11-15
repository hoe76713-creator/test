"""비상 기록표 대화. 기존 /ask에서 일반 라우팅보다 먼저 호출한다."""
import os
import re
import secrets
import threading
import time
from decimal import Decimal

FIELDS = (
    ('counterweight_runby', '균형추 런바이', 'mm', True),
    ('car_runby', '카 런바이', 'mm', False),
    ('main_worn', '주 로프 직경(마모)', 'mm', True),
    ('main_unworn', '주 로프 직경(비마모)', 'mm', True),
    ('car_gov_worn', '카측 조속기 로프 직경(마모)', 'mm', True),
    ('car_gov_unworn', '카측 조속기 로프 직경(비마모)', 'mm', True),
    ('up_speed', '상승 속도(무부하)', 'm/s', True),
    ('down_speed', '하강 속도(무부하)', 'm/s', True),
    ('up_current', '상승 전류(무부하)', 'A', True),
    ('down_current', '하강 전류(무부하)', 'A', True),
    ('cw_gov_worn', '균형추측 조속기 로프 직경(마모)', 'mm', False),
    ('cw_gov_unworn', '균형추측 조속기 로프 직경(비마모)', 'mm', False),
)
CANCEL = '비상 기록표 취소'
PREFIX = '비상 기록표 '
NUMBER = r'[0-9]{7}'
YEAR = r'[0-9]{4}'
GENERAL_COMMANDS = {
    '안전관리자', '안전관리자현황조회', '승강기정보조회', '보험·자체점검조회',
    '호기정보', '조회제원', '종합현황', '정밀검사', '최대런바이',
    '최대런바이계산', '다시계산하기', '기타부품계산', '계산기취소',
    '기타계산1', '기타계산2', '처음', '시작', '시작하기', '다시', '목차',
    '서울서부지사챗봇시작하기', '종료', '취소', '그만', '칭찬', '칭찬하고싶어요',
    '런바이', '호기별제원표', '건물호기정보', '보험점검조회', '보험가입확인',
    '자체점검확인', '자체점검일지조회', '검사이력조회', '최근검사지적사항',
    '연도계산', '정밀안전', '정밀안전검사', '정밀연장신청', '정밀안전검사연장에대해알아보기',
    '설치품목', '분동', '분동연락처', '공단의역활안내', '선임', '교육',
    '언제까지선임해야하나요', '언제까지선임해야해', '교육은언제까지받나',
    '답변다시', '최근답변', '기준계속', '이력계속',
}



def reply(text, actions=(), outputs=None):
    buttons = [{'label': label[:14], 'action': 'message', 'messageText': command}
               for label, command in actions]
    items = list(outputs or [])
    if not buttons:
        return {'version': '2.0', 'template': {'outputs': items or [{'simpleText': {'text': text}}]}}
    if not items and len(text.encode('utf-16-le')) // 2 > 400:
        items.append({'simpleText': {'text': text}})
        text = ''
    for i in range(0, len(buttons), 3):
        items.append({'textCard': {
            'description': (text if i == 0 else '') or '원하는 항목을 선택해 주세요.',
            'buttonLayout': 'vertical',
            'buttons': buttons[i:i + 3],
        }})
    return {'version': '2.0', 'template': {'outputs': items}}


GROUPS = {
    '기계실': (('up_speed','down_speed','up_current','down_current'), (3,4),
              '상승 속도(m/s) → 하강 속도(m/s) → 상승 전류(A) → 하강 전류(A)',
              '1.0044 1.0052 6.2 7.8\n기계실 청결상태 불량'),
    '승강로': (('main_worn','main_unworn','car_gov_worn','car_gov_unworn','cw_gov_worn','cw_gov_unworn'), (4,6),
              '주로프 → 카측 과속조절기 로프 → 균형추측 과속조절기 로프(선택)\n각 로프는 마모 → 비마모 순서(mm)로, 총 4개 또는 6개를 입력해 주세요.',
              '10.2 10.8 6.1 6.4\n승강로 조명 일부 미점등'),
    '피트': (('counterweight_runby','car_runby'), (1,2),
             '균형추 런바이(필수) → 카 런바이(선택), 단위 mm\n4자리 또는 7자리 숫자 하나를 측정값으로 보낼 때는 소수점을 붙여 주세요. 예: 2025.0',
             '170 220\n피트 바닥에 물 고임'),
}
ROPES = (('main','주로프'),('car_gov','카측 과속조절기 로프'),('cw_gov','균형추측 과속조절기 로프'))
VALUE = re.compile(r'(?:[0-9]{1,8}(?:\.[0-9]{1,4})?|\.[0-9]{1,4})')


class EmergencyRecord:
    def __init__(self, list_units, get_specs, allowed_specs, render, ttl=3600):
        self.list_units, self.get_specs, self.render = list_units, get_specs, render
        self.allowed_specs = frozenset(allowed_specs)
        self.ttl, self.states, self.lock = ttl, {}, threading.RLock()

    def handle(self, payload):
        req = payload.get('userRequest') or {}
        user = (req.get('user') or {}).get('id')
        raw = str(req.get('utterance') or '').strip()
        if not user:
            return None
        with self.lock:
            now = time.monotonic()
            for k in [k for k,v in self.states.items() if now-v['seen'] > self.ttl]:
                del self.states[k]
            if ''.join(raw.split()) in ('비상','비상기록표'):
                self.states[user] = dict(seen=now, token=secrets.token_hex(6), units=None,
                                         records={}, active=None, page=0)
                return reply('승강기 고유번호 7자리를 입력해 주세요.', [('취소', CANCEL)])
            s = self.states.get(user)
            if not s:
                return reply('비상 기록표를 먼저 입력해 주세요.') if raw.startswith(PREFIX) else None
            s['seen'] = now
            if raw == CANCEL:
                del self.states[user]
                return reply('비상 기록표 작성을 취소했습니다.')
            if not raw.startswith(PREFIX) and self.is_general_request(s, req, payload, raw):
                del self.states[user]
                return None  # 기존 챗봇에서 원래 요청을 그대로 처리한다.
            try:
                return self.step(s, raw)
            except (OSError, TimeoutError, ValueError, KeyError):
                return reply('기록표 작업을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.')

    @staticmethod
    def is_general_request(s, req, payload, raw):
        word = ''.join(raw.split()).rstrip('?？!.。')
        configured = {''.join(q.split()).rstrip('?？!.。')
                      for q in os.getenv('EMERGENCY_EXIT_UTTERANCES', '').split('|') if q.strip()}
        blocks = {part.strip() for part in os.getenv('EMERGENCY_EXIT_BLOCK_IDS', '').split(',') if part.strip()}
        block = req.get('block') or {}
        intent = payload.get('intent') or {}
        if any(isinstance(item, dict) and item.get('id') in blocks for item in (block, intent)):
            return True
        if (word in GENERAL_COMMANDS or word in configured or re.fullmatch(YEAR, raw)
                or word.startswith(('자격요건_', '[답변확인]'))
                or any(k in word for k in ('최대런바이', '선임자격', '자격진단'))
                or re.match(r'^[0-9]{7}\s*[^0-9.\s+-]', raw)):
            return True
        if s['units'] is None:
            return not re.fullmatch(NUMBER, raw)
        return bool(re.fullmatch(NUMBER, raw) or s['active'] is None)

    def button(self,s,n,label,action):
        return label, f'{PREFIX}{s["token"]} {n} {action}'

    def unit_list(self,s):
        units=list(s['units'].items())
        page=s['page']
        buttons=[self.button(s,n,f'{label[:6]}·{n}','선택') for n,label in units[page:page+6]]
        if page:
            buttons.append(self.button(s,'-','이전 호기','이전'))
        if page+6<len(units):
            buttons.append(self.button(s,'-','다음 호기','다음'))
        return reply('기록표를 작성할 호기를 선택해 주세요.', buttons)

    def title(self,n,r):
        return f'{r["label"]} · {n}'

    def menu(self,s,n,r):
        lines='\n'.join(f'{k}: {v}' for k,v in r['specs'].items())
        return reply((f'호기별 제원표 | {self.title(n,r)}\n\n'+lines)[:950],
                     [self.button(s,n,label,a) for label,a in (
                         ('측정값 입력하기','구역'),('제원 추가/수정','제원'),('기록표 출력','출력'))])

    def missing(self,r):
        return [g for g in GROUPS if g not in r['values']]

    def zones(self,s,n,r,all_zones=False):
        choices=list(GROUPS) if all_zones else self.missing(r)
        return [self.button(s,n,g,g) for g in choices]

    def prompt(self,n,r,g,edit=False):
        _,_,order,example=GROUPS[g]
        return reply(f'{self.title(n,r)} | {g} 측정값 {"수정" if edit else "입력"}\n\n'
                     f'측정값을 아래 순서대로 한 줄에 입력해 주세요.\n{order}\n\n'
                     '특이사항이 있으면 다음 줄에 적어 주세요.\n'
                     + ('특이사항만 보내면 기존 측정값은 유지됩니다.\n' if edit else '')
                     + f'\n예:\n{example}')

    def rated_speed(self,r):
        value = r['specs'].get('정격속도',r['specs'].get('정격 속도',''))
        m=re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*m/s',str(value).strip())
        return Decimal(m[1]) if m and Decimal(m[1])>0 else None

    def calculate(self,r,g):
        v=r['values'][g]
        notes, lines, computed = [], [], {}
        if g=='기계실':
            rated=self.rated_speed(r)
            warnings={}
            for prefix in ('up','down'):
                val=v[prefix+'_speed']
                if rated and val < rated*Decimal('.92'):
                    warnings[prefix]='92% 미만'
                elif rated and val > rated*Decimal('1.05'):
                    warnings[prefix]='105% 초과'
            for p,k in (('up','상승'),('down','하강')):
                lines.append(f'{k} 속도: {v[p+"_speed"]:.4f} m/s'+(' — ⚠️ 기준 범위 벗어남' if p in warnings else ''))
            for p,k in (('up','상승'),('down','하강')):
                lines.append(f'{k} 전류: {v[p+"_current"]} A')
            if len(warnings)==2 and warnings['up']==warnings['down']:
                notes.append(f'상승·하강 속도 정격속도의 {warnings["up"]}')
            else:
                notes += [f'{k} 속도 정격속도의 {warnings[p]}' for p,k in (('up','상승'),('down','하강')) if p in warnings]
            if rated is None:
                lines.append('정격속도(m/s)를 확인하지 못해 속도 범위 비교를 하지 않았습니다.')
            elif not notes:
                lines.append('상승·하강 속도 모두 설정 범위 이내입니다.')
        elif g=='승강로':
            for p,label in ROPES:
                if p+'_worn' not in v:
                    lines.append(f'{label}: 해당 없음')
                    continue
                a,b=v[p+'_worn'],v[p+'_unworn']
                ratio=(a*100/b)
                computed[p+'_remaining_percent']=f'{ratio:.2f}'
                lines.append(f'{label}: {a} / {b} mm — 잔존 직경 비율 {ratio:.2f}%'+(' ⚠️' if a*10<=b*9 else ''))
                if a*10<=b*9:
                    notes.append(f'{label} 마모율 '+('90% 미만' if a*10<b*9 else '90%'))
        else:
            lines=[f'균형추 런바이: {v["counterweight_runby"]} mm',
                   ('카 런바이: '+str(v['car_runby'])+' mm' if 'car_runby' in v else '카 런바이: 미입력')]
        return lines,notes,computed

    def result(self,s,n,r,g):
        lines,notes,_=self.calculate(r,g)
        notes+=r['notes'].get(g,[])
        buttons=[self.button(s,n,'결과 수정','수정'+g)]+self.zones(s,n,r)
        if not self.missing(r):
            buttons.append(self.button(s,n,'기록표 출력','출력'))
        return reply((f'{self.title(n,r)} | {g} 측정 결과 확인\n\n'+'\n'.join(lines)
                      +'\n\n특이사항: '+', '.join(notes)
                      +'\n\n입력값을 고치거나 특이사항을 추가하려면 [결과 수정]을 눌러 주세요.')[:950],buttons)

    def step(self,s,raw):
        if raw.startswith(PREFIX):
            parts=raw[len(PREFIX):].split()
            if len(parts)!=3 or parts[0]!=s['token']:
                return reply('이전 작성의 버튼입니다. 현재 작성 화면의 버튼을 눌러 주세요.')
            _,n,a=parts
            if s['units'] is None:
                return reply('승강기 고유번호를 먼저 입력해 주세요.')
            if n=='-' and a in ('이전','다음'):
                s['page']=max(0,min((len(s['units'])-1)//6*6,s['page']+(6 if a=='다음' else -6)))
                return self.unit_list(s)
            if n not in s['units']:
                return reply('현재 작성 목록에 없는 호기입니다.')
            if a=='선택':
                if n not in s['records']:
                    s['records'][n]=dict(label=s['units'][n],specs=dict(self.get_specs(n)),values={},notes={})
                s['active']=None
                return self.menu(s,n,s['records'][n])
            r=s['records'].get(n)
            if r is None:
                return reply('호기를 먼저 선택해 주세요.')
            s['active']=None
            if a=='다른호기':
                return self.unit_list(s)
            if a=='특이사항추가':
                s['active']=(n,'특이사항')
                return reply(f'{self.title(n,r)} | 특이사항 추가\n\n기록표에 추가할 특이사항을 입력해 주세요.',
                             [self.button(s,n,'기록표 다시 출력','출력')])
            if a=='구역':
                return reply(f'{self.title(n,r)} | 측정 구역 선택',self.zones(s,n,r,True))
            g=a.removeprefix('수정')
            if g in GROUPS:
                s['active']=(n,g)
                return self.prompt(n,r,g,g in r['values'])
            if a=='메뉴':
                return self.menu(s,n,r)
            if a in ('제원','제원재입력'):
                r.pop('pending',None)
                s['active']=(n,'제원')
                return reply(f'{self.title(n,r)} | 제원 추가/수정\n\n항목명: 값 형식으로 한 줄에 하나씩 입력해 주세요.\n예: 기계실 위치: 옥상',
                             [self.button(s,n,'제원표로 돌아가기','메뉴')])
            if a=='제원반영' and r.get('pending'):
                r['specs'].update(r.pop('pending'))
                return self.menu(s,n,r)
            if a=='출력':
                missing=self.missing(r)
                if missing:
                    return reply(f'{self.title(n,r)} | 미입력: '+', '.join(missing),self.zones(s,n,r))
                out,notes={},[]
                for g,v in r['values'].items():
                    out.update({k:str(x) for k,x in v.items()})
                    _,auto,computed=self.calculate(r,g)
                    out.update(computed)
                    notes.extend(dict.fromkeys(auto+r['notes'].get(g,[])))
                notes=list(dict.fromkeys(notes+r['notes'].get('특이사항',[])))
                specs=dict(r['specs'],호기=r['label'],특이사항='\n'.join(notes))
                outputs=self.render(n,specs,out)
                return reply('밑에 원하는 항목을 선택해 주세요.', [
                    self.button(s,n,'다른 호기 입력','다른호기'),
                    self.button(s,n,'제원 수정/추가','제원'),
                    self.button(s,n,'특이사항 추가','특이사항추가'),
                ], outputs=outputs)
            return self.menu(s,n,r)
        if s['units'] is None:
            if not re.fullmatch(NUMBER,raw):
                return reply('승강기 고유번호 7자리를 입력해 주세요.', [('취소',CANCEL)])
            units={n:label for n,label in self.list_units(raw) if re.fullmatch(NUMBER,n)}
            if not units:
                return reply('조회된 호기가 없습니다. 고유번호를 확인해 주세요.')
            s['units']=units
            return self.unit_list(s)
        active=s['active']
        if active is None:
            return reply('기록할 호기와 구역의 버튼을 먼저 눌러 주세요.') if re.match(r'^[.0-9]',raw) and not re.fullmatch(NUMBER,raw) else None
        n,g=active
        r=s['records'][n]
        if g=='특이사항':
            extra=[line.strip() for line in raw.splitlines() if line.strip()]
            merged=list(dict.fromkeys(r['notes'].get(g,[])+extra))
            if not extra or len('\n'.join(merged))>420:
                return reply('특이사항을 입력해 주세요. 추가 특이사항은 총 420자까지 기록할 수 있습니다.',
                             [self.button(s,n,'기록표 다시 출력','출력')])
            r['notes'][g]=merged
            s['active']=None
            return reply(f'{self.title(n,r)} | 특이사항을 추가했습니다.',
                         [self.button(s,n,'기록표 다시 출력','출력')])
        if g=='제원':
            pending={}
            for line in raw.splitlines():
                k,sep,v=map(str.strip,line.partition(':'))
                if not sep or k not in self.allowed_specs or not v or len(v)>150 or k in pending:
                    return reply('기록표에 있는 제원 항목을 중복 없이 “항목명: 값” 형식으로 입력해 주세요.')
                pending[k]=v
            if not pending:
                return reply('변경할 제원을 입력해 주세요.')
            r['pending']=pending
            s['active']=None
            return reply((f'{self.title(n,r)} | 제원 변경 내용 확인\n\n'+'\n'.join(
                f'{k}: {r["specs"].get(k) or "미입력"} → {v}' for k,v in pending.items()))[:950],
                [self.button(s,n,label,a) for label,a in [('기록표에 반영','제원반영'),('다시 입력','제원재입력'),('제원표로 돌아가기','메뉴')]])
        lines=[line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            return self.prompt(n,r,g,g in r['values'])
        keys,counts,_,_=GROUPS[g]
        first=lines[0].split()
        if re.match(r'^[+.-]?[0-9]',first[0]) and not re.search(r'[가-힣]',first[0]):
            if len(first) not in counts or not all(VALUE.fullmatch(x) for x in first):
                return self.prompt(n,r,g,g in r['values'])
            vals=list(map(Decimal,first))
            if g=='기계실' and len(vals)==3:
                vals.insert(0,vals[0])
            if g=='승강로':
                for i,(a,b) in enumerate(zip(vals[::2],vals[1::2])):
                    if not 0<a<b:
                        return reply(f'{self.title(n,r)} | {ROPES[i][1]}의 직경은 0 < 마모 < 비마모 순이어야 합니다. 값을 다시 입력해 주세요.')
            new=dict(zip(keys,vals))
            notes=lines[1:]
        else:
            new=None
            notes=lines
        existing=r['notes'].get(g,[])
        merged=list(dict.fromkeys(existing+notes))
        if sum(map(len,merged))+2*len(merged)>420:
            return reply('이 구역의 특이사항은 420자 이내로 입력해 주세요.')
        r['notes'][g]=merged
        if new is not None:
            r['values'][g]=new
        if g not in r['values']:
            return self.prompt(n,r,g)
        s['active']=None
        return self.result(s,n,r,g)
