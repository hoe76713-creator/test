"""기존 계산·조회를 보존하는 카카오 자연어 연결 모듈. Python 3.10+."""

import base64
import concurrent.futures
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent

RESET = {
    '서울서부지사챗봇시작하기',
    '시작하기',
    '처음',
    '다시',
    '종료',
    '취소',
    '그만',
}

LEGACY = {
    '다시계산하기',
    '계산기취소',
    '기타부품계산',
    '기타계산1',
    '기타계산2',
}

ACTION_NAMES = {
    'detail': '세부 설치기준',
    'history': '기준 변경 이력',
    'source': '관련 조항 확인',
    'application': '적용 시점 확인',
    'measurement': '측정 기준 확인',
    'inspection': '검사 항목 안내',
}

TOPIC_BUTTONS = {
    '카 상부': [
        ('카 상부 난간', 'detail'),
        ('상부틈새', 'measurement'),
        ('피난공간', 'detail'),
    ],
    '피트': [
        ('피트 사다리', 'detail'),
        ('하부틈새', 'measurement'),
        ('피트 정지장치', 'detail'),
    ],
    '기계실': [
        ('기계실 출입', 'detail'),
        ('기계실 작업공간', 'detail'),
        ('기계실 조명', 'detail'),
    ],
    '승강장': [
        ('승강장 문', 'detail'),
        ('승강장 잠금장치', 'detail'),
        ('승강장 조명', 'detail'),
    ],
    '카': [
        ('카 구조', 'detail'),
        ('카 내부 설비', 'detail'),
        ('카 상부', 'detail'),
    ],
}

ALIASES = {
    '피트난간': [
        '피트', '난간', '칸막이', '3.1.3(13)', '5.6', '6.5.5',
    ],
    '피트단차': [
        '피트', '난간', '칸막이', '3.1.3(13)',
    ],
    '피트이스탑': [
        '피트', '정지장치', '정지스위치',
    ],
    '기계실사다리': [
        '기계실', '출입 통로', '6.2.5',
    ],
}

# 표준화는 배포 자료에 포함하지 않고 검색 단계에서도 차단한다.
ALLOWED_CATEGORIES = {'law', 'addenda', 'inspection'}


def compact(s):
    return re.sub(r'\s+', '', s).lower()


def card(title, body, buttons=()):
    return {
        'basicCard': {
            'title': title,
            'description': body,
            'buttons': list(buttons),
            'buttonLayout': 'vertical',
        }
    }


def button(label, message):
    return {
        'action': 'message',
        'label': label[:14],
        'messageText': message,
    }


def envelope(outputs):
    return {
        'version': '2.0',
        'template': {'outputs': outputs},
    }


def notice(text, buttons=()):
    return envelope([
        card('승강기 검사 도우미', text, buttons)
    ])


def calculation_note(states, user):
    step = states.get(user, {}).get('step')
    return {
        'max_runby_input': (
            '최대 런바이 입력 대기 중입니다. '
            '숫자 6개를 입력하면 계산을 이어갑니다.'
        ),
        'other1_input': (
            '피난공간 높이 입력 대기 중입니다. '
            '측정값(m)을 입력하면 계산을 이어갑니다.'
        ),
        'other2_input': (
            '파이널 리미트 캠 입력 대기 중입니다. '
            '측정값(mm)을 입력하면 계산을 이어갑니다.'
        ),
    }.get(step, '')


class Corpus:
    """전 자료 검색 후 위치를 반환. 검색 결과와 원문 열람을 구분한다."""

    def __init__(self):
        self.sources = {
            x['id']: x
            for x in json.loads((BASE / 'catalog.json').read_text())
            if x['category'] in ALLOWED_CATEGORIES
        }
        self.pages = json.loads(
            (BASE / 'search_pages.json').read_text()
        )
        self.pages = [
            p for p in self.pages
            if p['source'] in self.sources
        ]
        self.lookup = {
            f"{p['source']}:{p['page']}": p
            for p in self.pages
        }

    def search(self, terms, offset=0):
        terms = [
            str(x)[:120]
            for x in terms[:20]
            if compact(str(x))
        ]
        expanded = list(terms)

        for key, vals in ALIASES.items():
            if any(key in compact(t) for t in terms):
                expanded.extend(vals)

        needles = set(compact(t) for t in expanded)
        hits = []

        for p in self.pages:
            norm = compact(p['text'])
            score = sum(norm.count(n) for n in needles)
            if not score:
                continue

            sid = p['source']
            src = self.sources[sid]
            lines = p['text'].splitlines()
            excerpts = [
                line.strip()
                for line in lines
                if any(n in compact(line) for n in needles)
            ]
            hits.append((
                score,
                {
                    'ref': f"{sid}:{p['page']}",
                    'filename': src['filename'],
                    'category': src['category'],
                    'excerpt': ' / '.join(excerpts)[:650],
                },
            ))

        hits.sort(key=lambda x: (-x[0], x[1]['ref']))
        offset = max(0, int(offset))

        return {
            'total': len(hits),
            'offset': offset,
            'next_offset': (
                offset + 30
                if offset + 30 < len(hits)
                else None
            ),
            'matches': [
                h[1] for h in hits[offset:offset + 30]
            ],
            'warning': (
                '검색 일치는 검토 완료가 아닙니다. '
                '관련 원문과 참조 조항을 열람하세요.'
            ),
        }

    def read(self, refs, visual=False):
        result, images = [], []

        for ref in refs[:5]:
            p = self.lookup.get(ref)
            if p is None:
                continue

            src = self.sources[p['source']]
            result.append({
                'ref': ref,
                'filename': src['filename'],
                'text': p['text'],
            })

            if visual:
                import fitz

                with fitz.open(BASE / src['path']) as pdf:
                    pix = pdf[p['page'] - 1].get_pixmap(
                        matrix=fitz.Matrix(1.4, 1.4)
                    )
                    images.append({
                        'type': 'input_image',
                        'image_url': (
                            'data:image/png;base64,'
                            + base64.b64encode(
                                pix.tobytes('png')
                            ).decode()
                        ),
                        'detail': 'high',
                    })

        return result, images


TOOLS = [
    {
        'type': 'function',
        'name': 'search_documents',
        'description': (
            '제공 법령·부칙·검사기준 전체 검색. '
            '종전 용어도 함께 검색한다.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'terms': {
                    'type': 'array',
                    'items': {'type': 'string'},
                },
                'offset': {'type': 'integer'},
            },
            'required': ['terms', 'offset'],
            'additionalProperties': False,
        },
        'strict': True,
    },
    {
        'type': 'function',
        'name': 'read_pages',
        'description': (
            '검색된 ref의 원문 열람. 부속서·그림·표·수치가 있으면 '
            'visual=true로 원본도 확인.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'refs': {
                    'type': 'array',
                    'items': {'type': 'string'},
                },
                'visual': {'type': 'boolean'},
            },
            'required': ['refs', 'visual'],
            'additionalProperties': False,
        },
        'strict': True,
    },
]

INSTRUCTIONS = '''승강기 검사기준 도우미다. 한국어로 답하라.
외부 지식·인터넷·기억 대신 도구가 반환한 제공 법령/부칙/검사기준만 근거로 쓴다.
표준화·협의체·기술자문·검토의견은 비활성화되어 있다. 인용하거나 그 해석을 재사용하지 않는다.
질문을 이해한 뒤 search_documents를 호출하고 관련 원문을 read_pages로 읽는다.
현재 제공 최신 법령은 2022 별표22. 표준화 연도와 최신 법령 연도를 혼동하지 않는다.
2009 통합본은 종전 이력이다. 2013 이후 용어가 달라도 관련 개념/조항을 추적한다.
피트 단차/난간은 2009 3.1.3(13), 이후 카 간 칸막이를 검토하되 적용 대상이 같은지 확인한다.
최신 수치·측정점은 최신 법령에서, 적용 시점·경과조치는 부칙에서 확인한다.
부속서 적합 요구는 해당 부속서/표/그림까지 읽고 확인한다. 숫자·표·그림은 visual=true로 확인한다.
변경 이력은 관련 판본과 부칙을 시간순으로 검토. 모든 관련 원문을 못 읽었다면 확인 범위를 명시하고 전수 검토 완료라 하지 않는다.
강도·계산 예시는 요청 때만. 권상식 우선. 유압식·포지티브식·상부틈새 완화는 요청 때만.
넓은 질문은 전체 핵심을 최대3개 카드로 나누고 추가 선택부터 요구하지 않는다.
애매한 난간 위치 등은 확인 질문. 직전 주제가 있으면 짧은 후속 질문을 연결한다.
날씨/음식 등 범위 밖은 지원 기능 안내. 실제 건물의 기계실 위치는 자료로 알 수 없으므로 관리실 확인 안내.
갇힘 등 실제 긴급상황은 비상통화와 관리주체/구조기관 연락 및 대기를 안내하고 문 강제개방·자가탈출 절차는 제공하지 않는다.
기존 조회·계산을 직접 계산하거나 조회결과를 지어내지 않는다.
최종 출력은 JSON 객체: {"kind":"answer|clarify|out_of_scope|selfcheck|insurance|runby","topic":"대상", "cards":[{"title":"제목","topic":"이 카드의 구체적 대상","body":"한국어 본문 270자 이내","refs":["source-12:59"]}]}.
answer 카드마다 실제 열람한 ref를 넣고 수치 근거와 일치시킨다. 본문에 임의 출처를 만들지 않는다.
clarify/out_of_scope는 ref 없어도 된다. 검색 실패와 근거 없음은 구분한다.
버튼은 서버가 고정 규칙으로 생성한다. 최대3카드. 제목 40자 이내. 본문은 자연스러운 한국어 문장만.
'''


class Bridge:
    def __init__(self, states):
        self.states = states
        self.sessions = {}
        self.jobs = {}
        self.lock = threading.RLock()
        self.pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=2
        )
        self.corpus = None

    def clear(self, user):
        with self.lock:
            self.states.pop(user, None)
            self.sessions.pop(user, None)
            for k in [
                k for k, v in self.jobs.items()
                if v['user'] == user
            ]:
                self.jobs.pop(k, None)

    def menu_buttons(self, topic):
        pairs = TOPIC_BUTTONS.get(topic)
        if pairs:
            return [
                button(t + ' 기준', '[기준조회] ' + t + ' | ' + a)
                for t, a in pairs
            ]
        return [
            button(
                ACTION_NAMES[a],
                '[기준조회] ' + topic + ' | ' + a,
            )
            for a in ('detail', 'application', 'history')
        ]

    def attach_note(self, reply, user):
        note = calculation_note(self.states, user)
        if note:
            out = reply['template']['outputs'][-1]
            if 'basicCard' in out:
                body = out['basicCard']['description']
                if len(body + '\n\n' + note) <= 400:
                    out['basicCard']['description'] = (
                        body + '\n\n' + note
                    )
        return reply

    def handle(self, content):
        """즉시 답변과 기존 함수로 전달할 대체 발화를 반환한다."""
        request = content.get('userRequest') or {}
        user = (request.get('user') or {}).get('id')
        raw = str(request.get('utterance') or '').strip()
        word = compact(raw)

        if not user:
            return notice(
                '사용자 식별정보를 확인하지 못했습니다. 다시 시도해 주세요.'
            ), None

        if len(raw) > 2000:
            return notice(
                '질문을 2,000자 이내로 입력해 주세요.'
            ), None

        if word in RESET:
            self.clear(user)
            outputs = []
            image = os.getenv('START_IMAGE_URL', '')
            if image.startswith('https://'):
                outputs.append({
                    'simpleImage': {
                        'imageUrl': image,
                        'altText': '서울서부지사 챗봇 시작',
                    }
                })
            outputs.append(card(
                '승강기 검사 통합 도우미',
                '승강기 고유번호 7자리 또는 궁금한 검사기준을 입력해 주세요.\n'
                '최대 런바이: 최대런바이\n'
                '정밀검사 연도: 설치 연도 4자리\n'
                '입력 대기와 저장값을 초기화했습니다.',
            ))
            return envelope(outputs), None

        if raw.startswith('[답변확인] '):
            return self.poll(
                raw.split(' ', 1)[1], user
            ), None

        session = self.sessions.setdefault(user, {})

        if re.match(r'^\d{7}(?!\d)', raw):
            # 새 고유번호 조회는 계산 대기·저장값을 초기화한다.
            self.states.pop(user, None)
            pending = session.pop('pending', None)
            if re.fullmatch(r'\d{7}', raw) and pending:
                raw += ' ' + {
                    'selfcheck': '자체점검',
                    'insurance': '보험가입확인',
                }[pending]
            session['last_id'] = raw[:7]
            return None, raw

        if (
            word in LEGACY
            or '최대런바이' in word
            or '자격요건_' in word
            or '선임자격' in word
            or '자격진단' in word
        ):
            session.pop('pending', None)
            return None, None

        numeric = (
            all(
                re.fullmatch(
                    r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?',
                    x,
                )
                for x in raw.split()
            )
            if raw else False
        )

        if numeric:
            if session.get('pending'):
                return notice(
                    '승강기 고유번호 7자리를 입력해 주세요.'
                ), None

            if (
                not calculation_note(self.states, user)
                and len(raw.split()) == 6
            ):
                return notice(
                    '최대런바이를 입력해 계산을 먼저 시작해 주세요.',
                    [button('최대 런바이 계산', '최대런바이')],
                ), None

            return None, None

        service = None
        if (
            '자체점검' in word
            and any(x in word for x in ('조회', '일지', '결과', '확인'))
            and not any(x in word for x in ('기준', '의무', '법령'))
        ):
            service = 'selfcheck'
        elif (
            '보험' in word
            and any(x in word for x in ('조회', '가입', '확인'))
            and not any(
                x in word
                for x in ('의무', '기준', '해야', '필수', '법령')
            )
        ):
            service = 'insurance'

        if service:
            session['pending'] = service
            name = (
                '자체점검 결과'
                if service == 'selfcheck'
                else '보험 가입 여부'
            )
            return self.attach_note(
                notice(
                    name + '를 조회할 승강기 고유번호 7자리를 입력해 주세요.'
                ),
                user,
            ), None

        if word == '보험':
            return self.attach_note(
                notice(
                    '어떤 보험 내용을 확인하시겠어요?',
                    [
                        button('보험 가입 여부 조회', '보험 조회'),
                        button('보험 가입 의무 기준', '보험 가입 의무 기준'),
                    ],
                ),
                user,
            ), None

        if not os.getenv('OPENAI_API_KEY'):
            return self.attach_note(
                notice(
                    '기준 검색 연결이 아직 설정되지 않았습니다. '
                    '기존 고유번호 조회와 계산은 사용할 수 있습니다.'
                ),
                user,
            ), None

        with self.lock:
            now = time.time()
            for k in [
                k for k, v in self.jobs.items()
                if now - v['created'] > 900
            ]:
                self.jobs.pop(k, None)

            if (
                len(self.jobs) >= 50
                or sum(
                    1 for j in self.jobs.values()
                    if not j['future'].done()
                ) >= 4
            ):
                return self.attach_note(
                    notice(
                        '검색 요청이 많습니다. 잠시 후 다시 시도해 주세요.'
                    ),
                    user,
                ), None

            jid = uuid.uuid4().hex
            future = self.pool.submit(
                self.answer, raw, session.get('topic', '')
            )
            self.jobs[jid] = {
                'user': user,
                'created': now,
                'future': future,
            }

        return self.attach_note(
            notice(
                '관련 법령과 적용 조건을 확인하고 있습니다. '
                '잠시 후 답변 확인을 눌러 주세요.',
                [button('답변 확인', '[답변확인] ' + jid)],
            ),
            user,
        ), None

    def poll(self, jid, user):
        job = self.jobs.get(jid)
        if not job or job['user'] != user:
            return notice(
                '조회가 만료되었거나 초기화되었습니다. '
                '질문을 다시 입력해 주세요.'
            )

        future = job['future']
        if not future.done():
            return self.attach_note(
                notice(
                    '관련 자료를 확인 중입니다. 잠시 후 다시 눌러 주세요.',
                    [button('답변 확인', '[답변확인] ' + jid)],
                ),
                user,
            )

        try:
            result = future.result()
        except Exception:
            logging.exception('standards lookup failed')
            return self.attach_note(
                notice(
                    '자료 검색 또는 답변 생성을 완료하지 못했습니다. '
                    '잠시 후 질문을 다시 입력해 주세요.'
                ),
                user,
            )

        kind = result.get('kind')
        if kind in ('selfcheck', 'insurance'):
            self.sessions.setdefault(user, {})['pending'] = kind
            return self.attach_note(
                notice('조회할 승강기 고유번호 7자리를 입력해 주세요.'),
                user,
            )

        if kind == 'runby':
            return notice(
                '최대런바이를 입력하면 기존 계산을 시작합니다.',
                [button('최대 런바이 계산', '최대런바이')],
            )

        topic = result.get('topic') or '승강기 기준'
        self.sessions.setdefault(user, {})['topic'] = topic

        outputs = []
        for c in result['cards']:
            outputs.append(card(
                c['title'],
                c['body'],
                self.menu_buttons(c.get('topic') or topic),
            ))

        return self.attach_note(envelope(outputs), user)

    def answer(self, question, previous_topic):
        if self.corpus is None:
            self.corpus = Corpus()

        inputs = [{
            'role': 'user',
            'content': json.dumps(
                {
                    'question': question,
                    'previous_topic': previous_topic,
                    'sources': [
                        {
                            'id': k,
                            'filename': v['filename'],
                            'category': v['category'],
                            'pages': v['pages'],
                        }
                        for k, v in self.corpus.sources.items()
                    ],
                },
                ensure_ascii=False,
            ),
        }]

        seen = set()
        searched = False

        for turn in range(8):
            payload = {
                'model': os.getenv('OPENAI_MODEL', 'gpt-6.1-sol'),
                'instructions': INSTRUCTIONS,
                'input': inputs,
                'tools': TOOLS,
                'max_output_tokens': 3500,
                'store': False,
            }
            response = requests.post(
                'https://api.openai.com/v1/responses',
                headers={
                    'Authorization': (
                        'Bearer ' + os.environ['OPENAI_API_KEY']
                    ),
                    'Content-Type': 'application/json',
                },
                json=payload,
                timeout=(5, 60),
            )
            response.raise_for_status()
            data = response.json()

            if data.get('status') != 'completed':
                raise RuntimeError('incomplete API response')

            logging.info(
                'openai usage=%s', data.get('usage', {})
            )
            items = data.get('output', [])
            inputs.extend(items)
            calls = [
                x for x in items
                if x.get('type') == 'function_call'
            ]

            if calls:
                for call in calls[:6]:
                    args = json.loads(call['arguments'])
                    images = []

                    if call['name'] == 'search_documents':
                        result = self.corpus.search(
                            args['terms'], args['offset']
                        )
                        searched = True
                    elif call['name'] == 'read_pages':
                        result, images = self.corpus.read(
                            args['refs'], args['visual']
                        )
                        seen.update(p['ref'] for p in result)
                    else:
                        result = {'error': 'unsupported tool'}

                    inputs.append({
                        'type': 'function_call_output',
                        'call_id': call['call_id'],
                        'output': json.dumps(
                            result, ensure_ascii=False
                        ),
                    })

                    if images:
                        inputs.append({
                            'role': 'user',
                            'content': [{
                                'type': 'input_text',
                                'text': (
                                    '직전 read_pages에 대응하는 '
                                    '원본 페이지 그림입니다.'
                                ),
                            }] + images,
                        })
                continue

            text = ''.join(
                p.get('text', '')
                for x in items
                if x.get('type') == 'message'
                for p in x.get('content', [])
                if p.get('type') == 'output_text'
            )
            result = json.loads(
                re.sub(
                    r'^```(?:json)?\s*|\s*```$',
                    '',
                    text.strip(),
                )
            )

            if result.get('kind') in (
                'selfcheck', 'insurance', 'runby'
            ):
                return result

            cards = result.get('cards', [])
            if not 1 <= len(cards) <= 3:
                raise ValueError('card count')

            for c in cards:
                refs = c.get('refs', [])

                if (
                    result.get('kind') == 'answer'
                    and (
                        not searched
                        or not refs
                        or not set(refs) <= seen
                    )
                ):
                    raise ValueError('unread citation')

                if any(r not in seen for r in refs):
                    raise ValueError('invalid citation')

                if len(c['body']) > 280 or len(c['title']) > 40:
                    raise ValueError('card too long')

                sources = []
                for ref in refs:
                    sid, page = ref.split(':')
                    name = self.corpus.sources[sid]['filename']
                    short = re.sub(
                        r'\(.*?\)', '', name
                    ).replace('.pdf', '')
                    sources.append(
                        short[:55] + ' ' + page + '쪽'
                    )

                if sources:
                    c['body'] += '\n근거: ' + ' / '.join(sources)

                if len(c['body']) > 320:
                    raise ValueError('citation overflow')

            return result

        raise RuntimeError(
            '검색 검토 회수 한도에 도달했습니다. '
            '전수검토 완료로 처리하지 않습니다.'
            )
