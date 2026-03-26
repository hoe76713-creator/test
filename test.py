from flask import Flask, request, jsonify
import requests
import xml.etree.ElementTree as ET
import datetime
import re
import os

app = Flask(__name__)

# API 설정
KEY = 'e3cee5ce5cec33048ec4e796503f3e3e2c17cf9bacae0079b46590af0d3ca1dd'
URLS = {
    'BULD': "http://apis.data.go.kr/B553664/BuldElevatorService/getBuldElvtrList",
    'SAFE': "http://apis.data.go.kr/B553664/ElevatorSafeMngrService/getSafeMngrList",
    'INSUR': "http://apis.data.go.kr/B553664/ElevatorInsuranceService/getElvtrInsurance",
    'CHECK': "http://apis.data.go.kr/B553664/ElevatorSelfCheckService/getSelfCheckList",
    'SPEC': "http://apis.data.go.kr/B553664/ElevatorOperationService/getOperationInfoListV1",
}

TODAY_DATE = "2026-03-26" # 요구사항 반영: 고정 날짜 또는 datetime.now().strftime('%Y-%m-%d') 사용

# --- 헬퍼 함수 ---
def get_api(url, params):
    try:
        res = requests.get(url, params=params, timeout=5.0)
        return ET.fromstring(res.content.strip()) if res.status_code == 200 else None
    except:
        return None

def format_dt(s):
    if not s or s in ["--", ""]:
        return "정보없음"
    c = s.replace("-", "").strip()
    return f"{c[:4]}년 {c[4:6]}월 {c[6:]}일" if len(c) == 8 else s

def get_info(no):
    root = get_api(URLS['BULD'], {'serviceKey': KEY, 'elevator_no': no})
    if root is not None and root.find('.//item') is not None:
        it = root.find('.//item')
        return {
            "buldNm": it.findtext('buldNm') or "정보없음",
            "addr": it.findtext('address1') or "주소 정보 없음",
            "asign": it.findtext('elvtrAsignNo') or "-"
        }
    return {"buldNm": "정보없음", "addr": "정보없음", "asign": "-"}

def get_building_elevators(addr):
    # 주소를 기반으로 해당 건물의 모든 승강기 목록을 가져옵니다 (페이징용)
    addr_short = " ".join(addr.split()[:3])
    root = get_api(URLS['BULD'], {'serviceKey': KEY, 'buld_address': addr_short, 'numOfRows': '100'})
    items = []
    if root is not None:
        for it in root.findall('.//item'):
            items.append({
                "no": it.findtext('elevatorNo') or "",
                "asign": it.findtext('elvtrAsignNo') or "-"
            })
    return [i for i in items if i['no']]

def kakao_res(outputs):
    return jsonify({"version": "2.0", "template": {"outputs": outputs}})

def kakao_simple_text(text):
    return kakao_res([{"simpleText": {"text": text}}])

# --- 리포트 함수 ---
def make_spec_report(no, info):
    addr_short = " ".join(info['addr'].split()[:3])
    root = get_api(URLS['SPEC'], {
        'serviceKey': KEY,
        'elevator_no': no,
        'buld_address': addr_short
    })
    if root is not None and root.find('.//item') is not None:
        it = root.find('.//item')
        return f"""⚙️ [호기별 제원표]
📍 호기: {info['asign']}호기 ({no})
📅 조회일: {TODAY_DATE}
━━━━━━━━━━━━━━
🏷️ 모델명: {it.findtext('elvtrModel') or '-'}
⚡ 정격속도: {it.findtext('ratedSpeed') or '-'}m/s
🏗️ 설치일: {format_dt(it.findtext('installationDe'))}
⚖️ 하중: {it.findtext('liveLoad')}kg
👨‍👩‍👧 정원: {it.findtext('ratedCap')}명
↕️ 층수: {it.findtext('shuttleFloorCnt')}층"""
    return "⚠️ 제원 조회 실패"

def make_check_report(no, info):
    res_list = []
    last_co = "확인불가"
    today = datetime.datetime.now()

    for i in range(3):
        ym = (today - datetime.timedelta(days=i * 31)).strftime("%Y%m")
        root = get_api(URLS['CHECK'], {
            'serviceKey': KEY,
            'elevator_no': no,
            'yyyymm': ym
        })
        if root is not None and root.find('.//item') is not None:
            it = root.find('.//item')
            last_co = (it.findtext('companyNm') or last_co).strip()
            res_list.append(f"""📅 {ym[:4]}년 {ym[4:]}월 점검
✅ 결과: {it.findtext('selchkResultNm')}
🛠️ 업체: {last_co}""")

    return f"""🔍 [자체점검일지 내역]
📍 {info['asign']}호기 ({no})
📅 조회일: {TODAY_DATE}
━━━━━━━━━━━━━━
{chr(10).join(res_list) if res_list else "⚠️ 점검 데이터 없음"}"""

def make_insur_report(no, info):
    today_ym = datetime.datetime.now().strftime("%Y%m")
    root = get_api(URLS['INSUR'], {
        'serviceKey': KEY,
        'elevator_no': no,
        'cont_ymd': today_ym
    })
    if root is not None and root.find('.//item') is not None:
        it = root.find('.//item')
        return f"""🛡️ [보험 가입 확인]
📍 {info['asign']}호기 ({no})
📅 조회일: {TODAY_DATE}
━━━━━━━━━━━━━━
🏢 보험사: {it.findtext('companyNm')}
⏰ 만료일: {format_dt(it.findtext('contEnDe'))}"""
    return f"⚠️ {no} 보험 정보 없음"


# --- 메인 라우터 ---
@app.route('/ask', methods=['POST'])
def ask():
    try:
        content = request.get_json()
        raw_msg = content['userRequest']['utterance']
        utterance = raw_msg.strip().replace(" ", "")
        all_digits = re.findall(r'\d+', raw_msg)
        elv_no = all_digits[0][:7] if all_digits else ""

        # =========================================================
        # [1] 안전관리자 현황 조회 (입력 유도)
        # =========================================================
        if "안전관리자현황조회" in utterance:
            return kakao_res([{
                "simpleText": {
                    "text": "고유번호 7자리를 입력해주세요.\n(예: 0152144)"
                }
            }])

        # =========================================================
        # [2] 보험 의무 안내
        # =========================================================
        if "보험꼭가입하나" in utterance or utterance == "보험가입":
            return kakao_res([{
                "basicCard": {
                    "title": "🛡️ 보험 가입 의무 안내",
                    "description": "승강기 책임보험은 법적 의무입니다.\n미가입 시 과태료가 부과됩니다.",
                    "buttons": [
                        {
                            "action": "message",
                            "label": "✅ 우리 건물 보험 확인",
                            "messageText": "보험조회"
                        }
                    ]
                }
            }])

        if "보험조회" in utterance:
            return kakao_res([{
                "simpleText": {
                    "text": "고유번호 7자리를 입력해주세요.\n(예: 보험_조회_0152144)"
                }
            }])

        # =========================================================
        # [3] 자체점검 안내
        # =========================================================
        if "자체점검필수인가" in utterance or utterance == "자체점검":
            return kakao_res([{
                "basicCard": {
                    "title": "🛠️ 자체점검 의무 안내",
                    "description": "승강기는 매월 1회 이상 자체점검을 실시해야 합니다.",
                    "buttons": [
                        {
                            "action": "message",
                            "label": "✅ 우리 점검 이력 확인",
                            "messageText": "자체점검조회"
                        }
                    ]
                }
            }])

        if "자체점검조회" in utterance:
            return kakao_res([{
                "simpleText": {
                    "text": "고유번호 7자리를 입력해주세요.\n(예: 자체점검_0152144)"
                }
            }])

        # =========================================================
        # [4] 정밀검사 안내
        # =========================================================
        if "정밀검사" in utterance:
            return kakao_res([{
                "basicCard": {
                    "title": "📅 정밀검사란?",
                    "description": "설치 후 15년이 경과한 승강기는 정밀안전검사 대상입니다.",
                    "buttons": [
                        {
                            "action": "message",
                            "label": "🔢 설치연도로 계산",
                            "messageText": "정밀검사_연도계산"
                        },
                        {
                            "action": "message",
                            "label": "🔍 고유번호로 조회",
                            "messageText": "정밀검사_번호조회"
                        }
                    ]
                }
            }])

        if "정밀검사_연도계산" in utterance:
            return kakao_res([{
                "simpleText": {
                    "text": "설치 연도 4자리를 입력해주세요.\n(예: 2010)"
                }
            }])

        if re.match(r'^\d{4}$', utterance):
            year = int(utterance)
            return kakao_res([{
                "simpleText": {
                    "text": f"[정밀검사 주기]\n\n"
                            f"✔️ 15년차: {year+15}년\n"
                            f"✔️ 18년차: {year+18}년\n"
                            f"✔️ 21년차: {year+21}년"
                }
            }])

        if "정밀검사_번호조회" in utterance:
            return kakao_res([{
                "simpleText": {
                    "text": "고유번호 7자리를 입력해주세요.\n(예: 정밀검사_조회_0152144)"
                }
            }])

        if "정밀검사_조회_" in utterance:
            elv_no = utterance.split("_")[-1]
            info = get_info(elv_no)
            return kakao_res([{
                "simpleText": {
                    "text": make_spec_report(elv_no, info)
                }
            }])

        # =========================================================
        # [5] 고유번호 기반 조회 (핵심 기능)
        # =========================================================
        if len(elv_no) == 7:
            info = get_info(elv_no)

            # 안전관리자 조회
            if "법적의무사항이행확인" in utterance:
                root = get_api(URLS['SAFE'], {'serviceKey': KEY, 'elevator_no': elv_no})
                name, end_de = "미등록", "정보없음"

                if root is not None:
                    items = root.findall('.//item')
                    if items:
                        m = items[-1]
                        name = m.findtext('safeMngrNm') or m.findtext('shuttleMngrNm') or "성함미상"
                        end_de = format_dt(m.findtext('valdEndDt') or m.findtext('eduEndDe'))

                desc = (f"✅ [안전관리자 선임 확인]\n"
                        f"건물명: {info['buldNm']}\n"
                        f"👤 성함: {name}\n"
                        f"🎓 만료: {end_de}")

                return kakao_res([{
                    "basicCard": {
                        "title": "⚖️ 법적 의무사항 이행 확인",
                        "description": desc,
                        "buttons": [
                            {"action": "message", "label": "📅 선임 기한 안내", "messageText": "언제까지선임해야하나요"},
                            {"action": "message", "label": "🎓 교육 이수 기준", "messageText": "교육은언제까지받나요"}
                        ]
                    }
                }])

            # 보험 조회
            if "보험_조회_" in utterance:
                return kakao_res([{
                    "simpleText": {
                        "text": make_insur_report(elv_no, info)
                    }
                }])

            # 자체점검 조회
            if "자체점검_" in utterance:
                return kakao_res([{
                    "simpleText": {
                        "text": make_check_report(elv_no, info)
                    }
                }])

        # =========================================================
        # 기본 fallback
        # =========================================================
        return kakao_res([{
            "simpleText": {
                "text": "❓ 고유번호 7자리를 입력해주세요."
            }
        }])

    except Exception as e:
        return kakao_res([{
            "simpleText": {
                "text": f"⚠️ 서버 오류: {str(e)}"
            }
        }])
