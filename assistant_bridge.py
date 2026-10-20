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
RESET = {'서울서부지사챗봇시작하기', '시작하기', '처음', '다시', '종료', '취소', '그만'}
LEGACY = {'다시계산하기', '계산기취소', '기타부품계산', '기타계산1', '기타계산2'}
ACTION_NAMES = {'detail': '세부 설치기준', 'history': '기준 변경 이력',
                'source': '관련 조항 확인', 'application': '적용 시점 확인',
                'measurement': '측정 기준 확인', 'inspection': '검사 항목 안내', 'overview': '항목 목록'}
TOPIC_BUTTONS = {
    '카 상부': [('카 상부 난간', 'detail'), ('상부틈새', 'measurement'), ('피난공간', 'detail')],
    '피트': [('피트 사다리', 'detail'), ('하부틈새', 'measurement'), ('피트 정지장치', 'detail')],
    '기계실': [('기계실 출입', 'detail'), ('기계실 작업공간', 'detail'), ('기계실 조명', 'detail')],
    '승강장': [('승강장 문', 'detail'), ('승강장 잠금장치', 'detail'), ('승강장 조명', 'detail')],
    '카': [('카 구조', 'detail'), ('카 내부 설비', 'detail'), ('카 상부', 'detail')],
}
ALIASES = {
    '상부틈새': ['꼭대기틈새', '3.1.3(7)', '5.7.1', '6.5.7'],
    '꼭대기틈새': ['상부틈새', '3.1.3(7)', '5.7.1', '6.5.7'],
    '발보호판': ['8.13.3.1', '8.7.2', '카지붕', '발보호판'],
    '피트난간': ['피트', '난간', '칸막이', '3.1.3(13)', '5.6', '6.5.5'],
    '피트단차': ['피트', '난간', '칸막이', '3.1.3(13)'],
    '피트이스탑': ['피트', '정지장치', '정지스위치'],
    '기계실조도': ['기계실', '조도', '조명', '3.1.5(6)', '6.3.7', '6.1.4'],
    '기계실조명': ['기계실', '조도', '조명', '3.1.5(6)', '6.3.7', '6.1.4'],
    '기계실사다리': ['기계실', '출입 통로', '6.2.5'],
}
# 표준화는 배포 자료에 포함하지 않고 검색 단계에서도 차단한다.
ALLOWED_CATEGORIES = {'law', 'addenda', 'inspection'}
LOG = logging.getLogger('elevator.search')
LOG.setLevel(logging.INFO)
if not LOG.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    LOG.addHandler(handler)
LOG.propagate = False

# 수치 자체는 이 표에 넣지 않는다. 카드 제목·순서와 검색어만 고정한다.
LAYOUTS = {
    '승강장': [('승강장 출입구 치수', '승강장 출입구', ['출입구 유효 높이', '출입구 유효 폭']),
              ('문틈·문턱 측정기준', '승강장 문틈·문턱', ['문짝 간 틈새', '문턱 사이의 수평']),
              ('승강장 조도 기준', '승강장 조명', ['승강장 조명'])],
    '기계실': [('출입문·접근통로 치수', '기계실 출입', ['기계실 출입문', '출입 통로', '출입문의 크기']),
              ('작업공간·환경 측정기준', '기계실 작업공간·조명', ['기계실 바닥', '작업구역', '조명 및 콘센트', '환기']),
              ('안전설비·설치요건', '기계실 안전설비', ['기계실', '주 개폐기', '잠금장치'])],
    '피트': [('출입문·사다리 치수', '피트 사다리', ['피트 출입문', '피트 사다리']),
           ('하부틈새·피난공간 치수', '하부틈새', ['하부 틈새', '피난공간', '피트 바닥']),
           ('정지장치·점검설비 설치기준', '피트 정지장치', ['피트', '정지장치', '점검운전 조작반'])],
    '카 상부': [('보호난간·발보호판 치수', '카 상부 난간', ['보호난간', '발보호판']),
              ('상부틈새·피난공간 치수', '상부틈새', ['상부 틈새', '피난공간']),
              ('점검·조명설비 설치기준', '카 상부 점검설비', ['카 지붕', '점검운전', '비상조명'])],
    '카': [('카 내부·출입구 치수', '카 구조', ['카 내부', '유효면적', '출입구 유효 높이']),
          ('조명·조작설비 측정기준', '카 내부 설비', ['카 조명', '조작버튼', '비상조명']),
          ('카 상부·하부 설치기준', '카 상부·하부', ['카 지붕', '보호난간', '하부 틈새'])],
}


def object_schema(properties):
    return {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}


STR = {'type': 'string'}
STRINGS = {'type': 'array', 'items': STR}
PLAN_SCHEMA = object_schema({'kind': {'type': 'string', 'enum': [
    'answer', 'clarify', 'out_of_scope', 'selfcheck', 'insurance', 'runby']},
    'topic': STR, 'terms': STRINGS, 'message': STR})
CARD_SCHEMA = object_schema({'title': STR, 'topic': STR, 'body': STR, 'refs': STRINGS})
ANSWER_SCHEMA = object_schema({'kind': {'type': 'string', 'enum': ['answer', 'clarify']},
    'topic': STR, 'cards': {'type': 'array', 'items': CARD_SCHEMA},
    'read_refs': STRINGS, 'visual_refs': STRINGS, 'search_terms': STRINGS})


class LookupFailure(RuntimeError):
    def __init__(self, code, message):
        super().__init__(code)
        self.code, self.public_message = code, message


def parse_question(raw, previous=''):
    action = 'detail'
    text = raw.strip()
    if text.startswith('[기준조회] '):
        text, sep, selected = text[len('[기준조회] '):].rpartition('|')
        if not sep or selected.strip() not in ACTION_NAMES:
