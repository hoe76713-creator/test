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
        utterance = raw_msg.strip()

        # ===== 1. 자격요건 (기존 기능 유지) =====
        if "자격요건" in utterance.replace(" ", ""):
            if "피난용_결과" in utterance:
                return kakao_simple_text("🚨 피난용 → 자격증 필수")
            return kakao_res([{
                "basicCard": {
                    "title": "🔍 [자격확인] 1단계",
                    "description": "피난용 엘리베이터인가요?",
                    "buttons": [
                        {"action": "message", "label": "예", "messageText": "자격요건_피난용_결과"},
                        {"action": "message", "label": "아니오", "messageText": "자격요건_진단_16층"}
                    ]
                }
            }])

        # ===== 2. 보험가입 릴레이 =====
        if "보험가입" in utterance:
            if "_" in utterance:
                parts = utterance.split("_")
                elv_no = parts[1] if len(parts) > 1 else ""
                page = int(parts[2]) if len(parts) > 2 else 1
                
                info = get_info(elv_no)
                elevators = get_building_elevators(info['addr'])
                start = (page - 1) * 15
                display = elevators[start:start+15]
                
                if not display:
                    return kakao_simple_text("호기 정보가 없습니다.")
                    
                cards = []
                for e in display:
                    cards.append({
                        "title": f"🛡️ {e['asign']}호기 ({e['no']})",
                        "description": f"조회일: {TODAY_DATE}\n보험 가입 이력을 확인합니다.",
                        "buttons": [{"action": "message", "label": "보험 리포트 보기", "messageText": f"조회보험_{e['no']}"}]
                    })
                
                if start + 15 < len(elevators):
                    cards.append({
                        "title": "🚀 다음 리스트",
                        "description": "다음 호기 목록",
                        "buttons": [{"action": "message", "label": "다음 15개 보기", "messageText": f"보험가입_{elv_no}_{page+1}"}]
                    })
                
                # 주의: 카카오톡 기본 Carousel 한도는 10개입니다. 초과 시 출력이 제한될 수 있습니다.
                return kakao_res([{"carousel": {"type": "basicCard", "items": cards[:10]}}])
            else:
                return kakao_res([{
                    "basicCard": {
                        "title": "❓ 보험 꼭 가입해야 하나요?",
                        "description": f"승강기 책임보험은 법적 의무입니다.\n(기준일: {TODAY_DATE})",
                        "buttons": [{"action": "message", "label": "✅ 우리 건물 보험 확인", "messageText": "고유번호 7자리를 입력해주세요. (예: 보험가입_0152144)"}]
                    }
                }])

        if "조회보험_" in utterance:
            elv_no = utterance.split("_")[1]
            info = get_info(elv_no)
            return kakao_simple_text(make_insur_report(elv_no, info))


        # ===== 3. 자체점검 릴레이 =====
        if "자체점검" in utterance:
            if "_" in utterance:
                parts = utterance.split("_")
                elv_no = parts[1] if len(parts) > 1 else ""
                page = int(parts[2]) if len(parts) > 2 else 1
                
                info = get_info(elv_no)
                elevators = get_building_elevators(info['addr'])
                start = (page - 1) * 15
                display = elevators[start:start+15]
                
                if not display:
                    return kakao_simple_text("호기 정보가 없습니다.")
                    
                cards = []
                for e in display:
                    cards.append({
                        "title": f"🛠️ {e['asign']}호기 ({e['no']})",
                        "description": f"조회일: {TODAY_DATE}\n자체점검 3개월 이력을 확인합니다.",
                        "buttons": [{"action": "message", "label": "점검 리포트 보기", "messageText": f"조회점검_{e['no']}"}]
                    })
                
                if start + 15 < len(elevators):
                    cards.append({
                        "title": "🚀 다음 리스트",
                        "description": "다음 호기 목록",
                        "buttons": [{"action": "message", "label": "다음 15개 보기", "messageText": f"자체점검_{elv_no}_{page+1}"}]
                    })
                return kakao_res([{"carousel": {"type": "basicCard", "items": cards[:10]}}])
            else:
                return kakao_res([{
                    "basicCard": {
                        "title": "🛠️ 자체점검 필수인가요?",
                        "description": f"매월 1회 이상 자체점검을 실시해야 합니다.\n(기준일: {TODAY_DATE})",
                        "buttons": [{"action": "message", "label": "✅ 우리 점검 이력 확인", "messageText": "고유번호 7자리를 입력해주세요. (예: 자체점검_0152144)"}]
                    }
                }])

        if "조회점검_" in utterance:
            elv_no = utterance.split("_")[1]
            info = get_info(elv_no)
            return kakao_simple_text(make_check_report(elv_no, info))


        # ===== 4. 정밀검사 릴레이 =====
        if "정밀검사" in utterance:
            if "조회_" in utterance:
                elv_no = utterance.split("_")[2]
                info = get_info(elv_no)
                return kakao_simple_text(make_spec_report(elv_no, info))
            else:
                return kakao_res([{
                    "basicCard": {
                        "title": "📅 정밀검사란 무엇인가요?",
                        "description": f"설치 후 15년이 경과한 승강기는 정밀안전검사 대상입니다.\n(기준일: {TODAY_DATE})",
                        "buttons": [
                            {"action": "message", "label": "🔢 설치연도로 계산하기", "messageText": "연도계산"},
                            {"action": "message", "label": "🔍 설치 날짜를 몰라요", "messageText": "고유번호 7자리를 입력해주세요. (예: 정밀검사_조회_0152144)"}
                        ]
                    }
                }])

        if "연도계산" in utterance:
            return kakao_simple_text("설치 연도 4자리를 입력해주세요. (예: 2010)")

        if re.match(r'^\d{4}$', utterance.strip()):
            year = int(utterance.strip())
            return kakao_simple_text(f"[{year}년 설치 승강기 주기 계산]\n기준일: {TODAY_DATE}\n- 15년차: {year+15}년\n- 18년차: {year+18}년\n- 21년차: {year+21}년")


        # ===== 5. 고유번호 단독 입력 (안전관리자 현황 및 기본 리스트) =====
        all_digits = re.findall(r'\d{7}', raw_msg)
        if all_digits:
            elv_no = all_digits[0]
            # '0152144 다음 리스트 2' 형태 파싱
            page_match = re.search(r'다음 리스트\s*(\d+)', utterance)
            page = int(page_match.group(1)) if page_match else 1
            
            info = get_info(elv_no)
            elevators = get_building_elevators(info['addr'])
            start = (page - 1) * 15
            display = elevators[start:start+15]
            
            cards = []
            if page == 1:
                cards.append({
                    "title": f"🏢 {info['buldNm']}",
                    "description": f"주소: {info['addr']}\n기준일: {TODAY_DATE}",
                    "buttons": [{"action": "message", "label": "👤 선임 정보 확인", "messageText": f"선임정보_{elv_no}"}]
                })
            
            for e in display:
                cards.append({
                    "title": f"📍 {e['asign']}호기 ({e['no']})",
                    "description": "원하시는 상세 정보를 선택하세요.",
                    "buttons": [
                        {"action": "message", "label": "점검", "messageText": f"조회점검_{e['no']}"},
                        {"action": "message", "label": "보험", "messageText": f"조회보험_{e['no']}"},
                        {"action": "message", "label": "제원", "messageText": f"정밀검사_조회_{e['no']}"}
                    ]
                })
                
            if start + 15 < len(elevators):
                cards.append({
                    "title": "🚀 다음 리스트",
                    "description": "다음 호기 목록",
                    "buttons": [{"action": "message", "label": "다음 보기", "messageText": f"{elv_no} 다음 리스트 {page+1}"}]
                })
                
            return kakao_res([{"carousel": {"type": "basicCard", "items": cards[:10]}}])

        return kakao_simple_text("정확한 키워드나 승강기 번호를 입력해주세요.")

    except Exception as e:
        return kakao_simple_text(f"⚠️ 오류 발생: {str(e)}")

# --- 실행 ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
