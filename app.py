from flask import Flask, request, jsonify, g
from assistant_bridge import Bridge
import requests
import xml.etree.ElementTree as ET
import datetime
import re
import math
import os

app = Flask(__name__)

# =========================================================
# 최대 런바이 / 기타 계산 사용자 상태 저장
# user_id별로 현재 계산 단계와 기존 입력값을 임시 저장합니다.
# =========================================================
runby_states = {}
bridge = Bridge(runby_states)

# API 설정
KEY = os.environ.get('PUBLIC_DATA_API_KEY', '')
URLS = {
    'BULD': "http://apis.data.go.kr/B553664/BuldElevatorService/getBuldElvtrList",
    'SAFE': "http://apis.data.go.kr/B553664/ElevatorSafeMngrService/getSafeMngrList",
    'INSUR': "http://apis.data.go.kr/B553664/ElevatorInsuranceService/getElvtrInsurance",
    'CHECK': "http://apis.data.go.kr/B553664/ElevatorSelfCheckService/getSelfCheckList",
    'SPEC': "http://apis.data.go.kr/B553664/ElevatorOperationService/getOperationInfoListV1",
}

# --- 헬퍼 함수 (조회 및 포맷) ---
def get_api(url, params):
    try:
        res = requests.get(url, params=params, timeout=5.0)
        return ET.fromstring(res.content.strip()) if res.status_code == 200 else None
    except: return None

def format_dt(s):
    if not s or s in ["--", ""]: return "정보없음"
    c = s.replace("-", "").strip()
    return f"{c[:4]}년 {c[4:6]}월 {c[6:]}일" if len(c) == 8 else s

def get_info(no):
    root = get_api(URLS['BULD'], {'serviceKey': KEY, 'elevator_no': no})
    if root is not None and root.find('.//item') is not None:
        it = root.find('.//item')
        return {
            "buldNm": it.findtext('buldNm') or "정보없음",
            "addr": it.findtext('address1') or "주소 정보 없음",
            "asign": it.findtext('elvtrAsignNo') or "-",
        }
    return {"buldNm": "정보없음", "addr": "정보없음", "asign": "-"}

def kakao_res(outputs):
    return jsonify({"version": "2.0", "template": {"outputs": outputs}})

# --- 리포트 생성 함수 ---
def make_spec_report(no, info):
    addr_short = " ".join(info['addr'].split()[:3])
    root = get_api(URLS['SPEC'], {'serviceKey': KEY, 'elevator_no': no, 'buld_address': addr_short})
    if root is not None and root.find('.//item') is not None:
        it = root.find('.//item')
        return (f"⚙️ [호기별 제원표]\n━━━━━━━━━━━━━━\n🏷️ 모델명: {it.findtext('elvtrModel') or '-'}\n💨 정격속도: {it.findtext('ratedSpeed') or '-'}m/s\n🏗️ 최초 설치일: {format_dt(it.findtext('installationDe'))}\n📦 하중: {it.findtext('liveLoad')}kg\n👨‍👩‍👧 정원: {it.findtext('ratedCap')}명\n↕️ 층수: {it.findtext('shuttleFloorCnt')}층")
    return "⚠️ 제원 조회 실패"

def make_check_report(no, info):
    res_list, last_co = [], "확인불가"
    today = datetime.datetime.now()
    for i in range(3):
        ym = (today - datetime.timedelta(days=i*31)).strftime("%Y%m")
        root = get_api(URLS['CHECK'], {'serviceKey': KEY, 'elevator_no': no, 'yyyymm': ym})
        if root is not None and root.find('.//item') is not None:
            it = root.find('.//item')
            last_co = (it.findtext('companyNm') or last_co).strip()
            res_list.append(f"📅 {ym[:4]}년 {ym[4:]}월 점검\n✅ 결과: {it.findtext('selchkResultNm')}\n🛠️ 업체: {last_co}")
    report = f"🛠️ [자체점검일지 내역]\n━━━━━━━━━━━━━━\n" + ("\n\n".join(res_list) if res_list else "⚠️ 점검 데이터 없음")
    return report

def make_insur_report(no, info):
    today_ym = datetime.datetime.now().strftime("%Y%m")
    root = get_api(URLS['INSUR'], {'serviceKey': KEY, 'elevator_no': no, 'cont_ymd': today_ym})
    if root is not None and root.find('.//item') is not None:
        it = root.find('.//item')
        return (f"🛡️ [보험 가입 확인]\n━━━━━━━━━━━━━━\n🏢 보험사: {it.findtext('companyNm')}\n⏳ 만료일: {format_dt(it.findtext('contEnDe'))}")
    return f"⚠️ {no} 보험 정보 없음"

def legacy_ask():
    try:
        content = request.get_json()
        raw_msg = getattr(g, 'routed_utterance', None) or content['userRequest']['utterance']
        utterance = raw_msg.strip().replace(" ", "")
        all_digits = re.findall(r'\d+', raw_msg)
        elv_no = all_digits[0][:7] if all_digits else ""

        # =========================================================
        # 📐 최대 런바이 / 기타 계산기
        # 기존 7자리 승강기 고유번호 처리보다 먼저 실행합니다.
        # =========================================================
        user_id = content['userRequest'].get('user', {}).get('id', 'default_user')
        calc_state = runby_states.get(user_id, {}).get('step')
        is_elevator_request = bool(re.match(r"^\s*\d{7}(?!\d)", raw_msg))

        # ---------------------------------------------------------
        # 1. "최대런바이" 시작
        # ---------------------------------------------------------
        if "최대런바이" in utterance:
            runby_states[user_id] = {
                "step": "max_runby_input"
            }

            guide_text = (
                "📏 [최대 런바이 계산기]\n\n"
                "아래 6개 항목을 띄어쓰기(공백)로 구분하여 순서대로 입력해 주세요.\n\n"
                "👉 순서: 레일(m) 부품(m) 난간(m) 완충기(mm) 속도(m/s) 런바이(mm)\n\n"
                "예시)\n"
                "1 2 3 66 1 170"
            )

            return kakao_res([{
                "simpleText": {
                    "text": guide_text
                }
            }])

        # ---------------------------------------------------------
        # 2. "다시 계산하기" 버튼
        # ---------------------------------------------------------
        if utterance == "다시계산하기":
            # 다시 계산하기에서는 아직 계산 입력 상태로 들어가지 않습니다.
            # 사용자가 원하는 계산 종류를 선택하도록 하고,
            # 취소를 누르면 계산기 상태를 완전히 종료하여
            # 7자리 승강기 고유번호 조회가 정상 작동하도록 합니다.
            saved = runby_states.get(user_id, {})
            if saved:
                saved["step"] = "recalculate_menu"
                runby_states[user_id] = saved
            else:
                runby_states[user_id] = {"step": "recalculate_menu"}

            guide_text = (
                "📏 [최대 런바이 계산기]\n\n"
                "다시 계산할 항목을 선택해 주세요.\n\n"
                "⚠️ 계산 중에도 7자리 고유번호를 입력하면 조회할 수 있습니다.\n"
                "계산기 입력 상태를 종료하려면 '❌ 취소'를 눌러 주세요."
            )

            return kakao_res([{
                "basicCard": {
                    "title": "📏 최대 런바이 계산기",
                    "description": guide_text,
                    "buttons": [
                        {
                            "action": "message",
                            "label": "📏 최대 런바이 계산",
                            "messageText": "최대런바이계산"
                        },
                        {
                            "action": "message",
                            "label": "📐 기타 부품 계산",
                            "messageText": "기타 부품 계산"
                        },
                        {
                            "action": "message",
                            "label": "❌ 취소",
                            "messageText": "계산기취소"
                        }
                    ]
                }
            }])

        # ---------------------------------------------------------
        # 2-1. 다시 계산하기 메뉴에서 "최대 런바이 계산" 선택
        # ---------------------------------------------------------
        if utterance == "최대런바이계산":
            saved = runby_states.get(user_id, {})
            saved["step"] = "max_runby_input"
            runby_states[user_id] = saved

            guide_text = (
                "📏 [최대 런바이 계산기]\n\n"
                "아래 6개 항목을 띄어쓰기(공백)로 구분하여 순서대로 입력해 주세요.\n\n"
                "👉 순서: 레일(m) 부품(m) 난간(m) 완충기(mm) 속도(m/s) 런바이(mm)\n\n"
                "예시)\n"
                "1 2 3 66 1 170"
            )

            return kakao_res([{
                "simpleText": {
                    "text": guide_text
                }
            }])

        # ---------------------------------------------------------
        # 2-2. 계산기 "취소" → 계산기 상태 완전 초기화
        # ---------------------------------------------------------
        if utterance == "계산기취소":
            runby_states.pop(user_id, None)

            return kakao_res([{
                "basicCard": {
                    "title": "🏠 계산기 종료",
                    "description": (
                        "최대 런바이 계산기를 종료했습니다.\n\n"
                        "이제 7자리 승강기 고유번호를 입력하면 기존 조회 기능을 사용할 수 있습니다."
                    ),
                    "buttons": [
                        {
                            "action": "message",
                            "label": "🏠 처음으로",
                            "messageText": "처음"
                        }
                    ]
                }
            }])

        # ---------------------------------------------------------
        # 4. 기타 부품 계산 메뉴
        # ---------------------------------------------------------
        if utterance == "기타부품계산":
            saved = runby_states.get(user_id, {})

            if "buffer_m" not in saved or "speed" not in saved:
                return kakao_res([{
                    "simpleText": {
                        "text": (
                            "⚠️ 먼저 최대 런바이 계산을 완료해 주세요.\n\n"
                            "'최대런바이'를 입력하면 계산을 시작할 수 있습니다."
                        )
                    }
                }])

            runby_states[user_id]["step"] = "other_menu"

            return kakao_res([{
                "basicCard": {
                    "title": "📐 [기타 부품 계산]",
                    "description": "계산할 항목을 선택해 주세요.",
                    "buttons": [
                        {
                            "action": "message",
                            "label": "① 피난공간 높이",
                            "messageText": "기타계산1"
                        },
                        {
                            "action": "message",
                            "label": "② 파이널 리미트 캠",
                            "messageText": "기타계산2"
                        }
                    ]
                }
            }])


        # ---------------------------------------------------------
        # 3. 최대 런바이 6개 값 입력
        # ---------------------------------------------------------
        if runby_states.get(user_id, {}).get("step") == "max_runby_input" and not is_elevator_request:
            raw_values = raw_msg.strip().split()

            if len(raw_values) != 6:
                return kakao_res([{
                    "simpleText": {
                        "text": (
                            "⚠️ 입력 형식이 올바르지 않습니다.\n\n"
                            "6개 수치를 띄어쓰기로 구분하여 다시 입력해 주세요.\n\n"
                            "👉 순서: 레일(m) 부품(m) 난간(m) 완충기(mm) 속도(m/s) 런바이(mm)\n\n"
                            "예시) 1 2 3 66 1 170"
                        )
                    }
                }])

            try:
                rail = float(raw_values[0])
                part = float(raw_values[1])
                handrail = float(raw_values[2])
                buffer_mm = float(raw_values[3])
                v = float(raw_values[4])
                runby_mm = float(raw_values[5])

                if min(rail, part, handrail, buffer_mm, v, runby_mm) < 0 or not all(math.isfinite(x) for x in (rail, part, handrail, buffer_mm, v, runby_mm)):
                    raise ValueError

                # 완충기 행정은 입력 단위가 mm이므로 계산용 m로 변환
                buffer_m = buffer_mm / 1000.0
                runby_m = runby_mm / 1000.0

                # 속도에 따른 공통 보정값
                speed_term = 0.035 * (v ** 2)

                # ① 레일
                rail_result_m = (
                    rail
                    - runby_m
                    - buffer_m
                    - (0.1 + speed_term)
                )

                # ② 부품(A·B)
                part_result_m = (
                    part
                    - runby_m
                    - buffer_m
                    - (0.5 + speed_term)
                )

                # ③ 난간(D)
                handrail_result_m = (
                    handrail
                    - runby_m
                    - buffer_m
                    - (0.3 + speed_term)
                )

                # 이후 기타 계산에서 재사용할 값 저장
                runby_states[user_id] = {
                    "step": "max_runby_result",
                    "buffer_mm": buffer_mm,
                    "buffer_m": buffer_m,
                    "runby_mm": runby_mm,
                    "runby_m": runby_m,
                    "speed": v,
                    "rail": rail,
                    "part": part,
                    "handrail": handrail
                }

                # 계산 결과 A와 입력 런바이 B를 각 항목별로 비교
                # A >= B이면 적합, A < B이면 불량
                rail_result_mm = round(rail_result_m * 1000)
                part_result_mm = round(part_result_m * 1000)
                handrail_result_mm = round(handrail_result_m * 1000)

                def judge_runby(result_mm):
                    status = "✅ 적합" if result_mm >= runby_mm else "❌ 불량"
                    warning = "\n⚠️ 계산값 100mm 이하 경고" if result_mm <= 100 else ""
                    return status + warning

                overall_ok = all(
                    value >= runby_mm
                    for value in (rail_result_mm, part_result_mm, handrail_result_mm)
                )
                overall_status = "✅ 양호" if overall_ok else "❌ 불합"

                result_text = (
                    "📏 [최대 런바이 계산 결과]\n\n"
                    f"입력 런바이(B): {runby_mm:g} mm\n\n"
                    "🔹 레일\n"
                    f"{rail:g} - {runby_mm:g}/1000 - {buffer_mm:g}/1000 - (0.1 + 0.035*{v:g}^2) "
                    f"= {rail_result_mm:.0f} mm\n"
                    f"{judge_runby(rail_result_mm)}\n\n"
                    "🔹 부품(A·B)\n"
                    f"{part:g} - {runby_mm:g}/1000 - {buffer_mm:g}/1000 - (0.5 + 0.035*{v:g}^2) "
                    f"= {part_result_mm:.0f} mm\n"
                    f"{judge_runby(part_result_mm)}\n\n"
                    "🔹 난간(D)\n"
                    f"{handrail:g} - {runby_mm:g}/1000 - {buffer_mm:g}/1000 - (0.3 + 0.035*{v:g}^2) "
                    f"= {handrail_result_mm:.0f} mm\n"
                    f"{judge_runby(handrail_result_mm)}\n\n"
                    f"🏁 최종 판정: {overall_status}"
                )

                return kakao_res([{
                    "basicCard": {
                        "title": "📏 최대 런바이 계산 결과",
                        "description": result_text,
                        "buttons": [
                            {
                                "action": "message",
                                "label": "🔄 다시 계산하기",
                                "messageText": "다시 계산하기"
                            },
                            {
                                "action": "message",
                                "label": "📐 기타 부품 계산",
                                "messageText": "기타 부품 계산"
                            }
                        ]
                    }
                }])

            except (ValueError, TypeError):
                return kakao_res([{
                    "simpleText": {
                        "text": (
                            "⚠️ 숫자 입력을 확인해 주세요.\n\n"
                            "예시) 1 2 3 66 1 170"
                        )
                    }
                }])

        # ---------------------------------------------------------
        # 5. 기타 계산 1 - 피난공간 높이
        # ---------------------------------------------------------
        if utterance == "기타계산1":
            if user_id not in runby_states or "buffer_m" not in runby_states[user_id]:
                return kakao_res([{
                    "simpleText": {
                        "text": "⚠️ 먼저 최대 런바이 계산을 완료해 주세요."
                    }
                }])

            runby_states[user_id]["step"] = "other1_input"

            return kakao_res([{
                "simpleText": {
                    "text": (
                        "📐 [피난공간 높이 계산]\n\n"
                        "피난공간의 측정값을 입력해 주세요.\n"
                        "👉 측정값은 m 단위로 입력해 주세요.\n\n"
                        "예시) 4"
                    )
                }
            }])

        # ---------------------------------------------------------
        # 6. 기타 계산 1 - 측정값 입력 및 판정
        # ---------------------------------------------------------
        if runby_states.get(user_id, {}).get("step") == "other1_input" and not is_elevator_request:
            try:
                measured_m = float(raw_msg.strip())

                saved = runby_states[user_id]
                result_m = (
                    measured_m
                    - saved["runby_m"]
                    - saved["buffer_m"]
                    - (0.035 * (saved["speed"] ** 2))
                )

                if result_m < 2.0:
                    guidance = (
                        "⚠️ 계산 높이가 2 m 미만입니다.\n"
                        "해당 피난공간은 **웅크린 자세로 피난할 수 있도록 "
                        "0.5 m × 0.7 m 이상의 공간**을 확보해야 합니다."
                    )
                else:
                    guidance = (
                        "✅ 계산 높이가 2 m 이상입니다.\n"
                        "해당 피난공간은 **서 있는 자세로 피난할 수 있도록 "
                        "0.4 m × 0.5 m 이상의 공간**을 확보해야 합니다."
                    )

                runby_states[user_id]["step"] = "max_runby_result"

                result_text = (
                    "📐 [피난공간 높이 계산 결과]\n\n"
                    f"계산식\n"
                    f"{measured_m:g} - {saved['runby_mm']:g}/1000 - {saved['buffer_mm']:g}/1000 - "
                    f"(0.035*{saved['speed']:g}^2) = {result_m:.3f} m\n\n"
                    f"{guidance}"
                )

                return kakao_res([{
                    "basicCard": {
                        "title": "📐 피난공간 높이 계산 결과",
                        "description": result_text,
                        "buttons": [
                            {
                                "action": "message",
                                "label": "📐 기타 부품 계산",
                                "messageText": "기타 부품 계산"
                            },
                            {
                                "action": "message",
                                "label": "🔄 다시 계산하기",
                                "messageText": "다시 계산하기"
                            }
                        ]
                    }
                }])

            except (ValueError, TypeError):
                return kakao_res([{
                    "simpleText": {
                        "text": "⚠️ 숫자로 입력해 주세요.\n예시) 4"
                    }
                }])

        # ---------------------------------------------------------
        # 7. 기타 계산 2 - 파이널 리미트 캠 길이
        # ---------------------------------------------------------
        if utterance == "기타계산2":
            if user_id not in runby_states or "buffer_m" not in runby_states[user_id]:
                return kakao_res([{
                    "simpleText": {
                        "text": "⚠️ 먼저 최대 런바이 계산을 완료해 주세요."
                    }
                }])

            runby_states[user_id]["step"] = "other2_input"

            return kakao_res([{
                "simpleText": {
                    "text": (
                        "📐 [파이널 리미트 캠 길이 계산]\n\n"
                        "① 리미트 상부에서 캠 끝단까지의 거리를 측정해 주세요.\n"
                        "최대 런바이 계산에서 입력한 런바이를 자동으로 사용합니다.\n\n"
                        "👉 입력: 리미트 상부~캠 끝단(mm)\n\n"
                        "예시) 350"
                    )
                }
            }])

        # ---------------------------------------------------------
        # 8. 기타 계산 2 - 측정값 1개 입력 및 판정
        # ---------------------------------------------------------
        if runby_states.get(user_id, {}).get("step") == "other2_input" and not is_elevator_request:
            raw_values = raw_msg.strip().split()

            if len(raw_values) != 1:
                return kakao_res([{
                    "simpleText": {
                        "text": (
                            "⚠️ 입력 형식이 올바르지 않습니다.\n\n"
                            "캠 측정거리 1개를 mm 단위로 입력해 주세요.\n\n"
                            "👉 입력: 리미트 상부~캠 끝단(mm)\n\n"
                            "예시) 350"
                        )
                    }
                }])

            try:
                limit_to_cam_mm = float(raw_values[0])
                counterweight_runby_mm = runby_states[user_id]["runby_mm"]

                if limit_to_cam_mm < 0 or not math.isfinite(limit_to_cam_mm):
                    raise ValueError

                saved = runby_states[user_id]

                # 0.035 × v²는 m 단위이므로 mm로 변환
                speed_term_mm = 0.035 * (saved["speed"] ** 2) * 1000

                result_mm = (
                    limit_to_cam_mm
                    - counterweight_runby_mm
                    - saved["buffer_mm"]
                    - speed_term_mm
                )

                # 현재 측정 조건에서 허용되는 최대 균형추 런바이
                # 결과값이 정확히 0 mm가 되는 지점
                max_counterweight_runby_mm = (
                    limit_to_cam_mm
                    - saved["buffer_mm"]
                    - speed_term_mm
                )

                if result_mm < 0:
                    judgment = (
                        "❌ **부적합**\n"
                        "캠의 여유 길이가 확보되지 않습니다. "
                        "파이널 리미트가 캠을 벗어날 가능성이 있으므로 확인이 필요합니다."
                    )
                elif result_mm <= 10:
                    judgment = (
                        "⚠️ **적합(주의)**\n"
                        "캠의 여유 길이가 10 mm 이하로 짧습니다. "
                        "캠의 여유 길이가 충분한지 확인해 주세요."
                    )
                else:
                    judgment = (
                        "✅ **적합**\n"
                        "캠의 여유 길이가 충분히 확보되어 있습니다."
                    )

                runby_states[user_id]["step"] = "max_runby_result"

                result_text = (
                    "📐 [파이널 리미트 캠 길이 계산 결과]\n\n"
                    "계산식\n"
                    f"{limit_to_cam_mm:g} - {counterweight_runby_mm:g} - "
                    f"{saved['buffer_mm']:g} - ((0.035*{saved['speed']:g}^2)*1000) "
                    f"= {result_mm:.0f} mm\n\n"
                    f"{judgment}\n\n"
                    "최대 균형추 런바이\n"
                    f"{counterweight_runby_mm:.0f} + {result_mm:.0f} "
                    f"= {max_counterweight_runby_mm:.0f} mm"
                )

                return kakao_res([{
                    "basicCard": {
                        "title": "📐 파이널 리미트 캠 길이 계산 결과",
                        "description": result_text,
                        "buttons": [
                            {
                                "action": "message",
                                "label": "📐 기타 부품 계산",
                                "messageText": "기타 부품 계산"
                            },
                            {
                                "action": "message",
                                "label": "🔄 다시 계산하기",
                                "messageText": "다시 계산하기"
                            }
                        ]
                    }
                }])

            except (ValueError, TypeError):
                return kakao_res([{
                    "simpleText": {
                        "text": "⚠️ 숫자로 입력해 주세요.\n예시) 350"
                    }
                }])

        # =========================================================
        # 4자리 숫자(설치 연도)를 입력했을 때 자동으로 주기 계산
        # =========================================================
        if utterance.isdigit() and len(utterance) == 4:
            install_year = int(utterance)
            y15 = install_year + 15
            y16 = install_year + 16 # 1차
            y19 = y16 + 3           # 2차
            y22 = y19 + 3           # 3차
            y25 = install_year + 25 # 노후 주기 진입
            
            report = (
                f"📅 [정밀검사 대상 기간 확인]\n"
                f"🏗️ 승강기 설치 연도: {install_year}년\n"
                f"━━━━━━━━━━━━━━\n"
                f"❓ 왜 {y16}년차가 정밀안전검사인가요?\n"
                f"법적으로 '만 15년이 완전히 지난 날'부터 대상이 되기 때문입니다.\n\n"
                f"• {y15}년 검사 신청일에는 아직 15년이 채워지지 않아 '정기검사'를 받습니다.\n"
                f"• 설치한 날로부터 15년이 지난 **{y16}년 접수분**부터 첫 번째 '정밀안전검사'가 시작됩니다.\n\n"
                f"📋 [연도별 검사 스케줄]\n"
                f"정밀안전검사는 첫 검사 이후 '3년 주기'로 실시합니다.\n"
                f"1️⃣ 제1차 정밀검사 : {y16}년\n"
                f"2️⃣ 제2차 정밀검사 : {y19}년 (3년 후)\n"
                f"3️⃣ 제3차 정밀검사 : {y22}년 (3년 후)\n\n"
                f"🚨 [노후 승강기 특별관리]\n"
                f"설치 후 {y25}년(25년 경과)부터는 안전을 위해 검사 주기가 '6개월'로 짧아집니다.\n\n"
                f"📍 25년 이후 검사 방식\n"
                f"노후 승강기는 정밀검사 주기(3년)에 맞춰 다음과 같이 검사를 받게 됩니다.\n"
                f"• 정기검사 : 총 5회 (6개월마다 실시)\n"
                f"• 정밀검사 : 총 1회 (3년마다 실시)\n"
                f"6개월마다 정기검사를 받다가, 3년째 되는 해에 정밀안전검사를 받습니다.\n"
                f"━━━━━━━━━━━━━━\n"
                f"🔍 정확한 설치일자는 '제원 조회'에서 확인하세요!")
                
            return kakao_res([{"basicCard": {"description": report,"buttons": [
                    {"action": "message", "label": "🛠️ 설치 품목 확인", "messageText": "설치 품목 확인"},
                    {"action": "message", "label": "🗓️ 정밀연장 신청 안내", "messageText": "정밀연장 신청 안내"}]}}])

        # =========================================================
        # 자격요건 진단 (이모지 교체: 🔍 -> 🏅)
        # =========================================================
        if "선임자격" in utterance or "자격진단" in utterance:
            if "진단_" not in utterance and "결과" not in utterance:
                return kakao_res([{"basicCard": {"title": "🏅 [자격진단] 1단계", "description": "화재 시 대피용으로 지정된\n'피난용 엘리베이터'를 관리하시나요?", "buttons": [
                    {"action": "message", "label": "예 (피난용 있음)", "messageText": "자격요건_피난용_결과"},
                    {"action": "message", "label": "아니오", "messageText": "자격요건_진단_16층"}]}}])

        if "자격요건_진단_16층" in utterance:
            return kakao_res([{"basicCard": {"title": "🏅 [자격진단] 2단계", "description": "건축물의 '지상층'이 16층 이상인가요?\n(※ 지하층은 제외합니다)", "buttons": [
                {"action": "message", "label": "예 (지상 16층 이상)", "messageText": "자격요건_다중이용_결과"},
                {"action": "message", "label": "아니오", "messageText": "자격요건_진단_문화집회"}]}}])

        diag_flow = [
            ("문화집회", "문화 및 집회시설에 해당하시나요?\n(동물원 및 식물원 제외)", "종교"),
            ("종교", "종교시설에 해당하시나요?", "판매"),
            ("판매", "판매시설에 해당하시나요?", "여객"),
            ("여객", "운수시설 중 여객용 시설에 해당하시나요?", "종합병원"),
            ("종합병원", "의료시설 중 종합병원에 해당하시나요?", "관광숙박"),
            ("관광숙박", "숙박시설 중 관광숙박시설에 해당하시나요?", "일반결정")
        ]
        for current, quest, next_step in diag_flow:
            if f"자격요건_진단_{current}" in utterance:
                target_next = "자격요건_일반_결과" if next_step == "일반결정" else f"자격요건_진단_{next_step}"
                return kakao_res([{"basicCard": {"title": "🏅 용도 확인", "description": quest, "buttons": [
                    {"action": "message", "label": "예", "messageText": "자격요건_진단_면적"},
                    {"action": "message", "label": "아니오", "messageText": target_next}]}}])

        if "자격요건_진단_면적" in utterance:
            return kakao_res([{"basicCard": {"title": "📐 바닥면적 확인", "description": "해당 용도로 쓰는 바닥면적의 합계가\n5,000제곱미터 이상인가요?", "buttons": [
                {"action": "message", "label": "예 (5,000㎡ 이상)", "messageText": "자격요건_다중이용_결과"},
                {"action": "message", "label": "아니오 (5,000㎡ 미만)", "messageText": "자격요건_일반_결과"}]}}])

        # --- 자격요건 결과값 버튼 명칭 구조도 반영 ---
        if "자격요건_피난용_결과" in utterance:
            desc = ("🚨 [피난용 엘리베이터 자격 안내]\n━━━━━━━━━━━━━━\n해당 승강기는 화재 시 인명 구조용으로 사용되므로 **일반 교육만으로는 선임이 불가능**합니다.\n\n"
                    "✅ **1. 필수 자격 요건 (중 하나)**\n1️⃣ 자격증: 승강기·기계·전기·전자 분야 기능사 이상\n2️⃣ 학위: 관련 학과(기계·전기 등) 전문학사 이상 졸업\n3️⃣ 경력: 승강기 관련 분야 6개월 이상의 실무 경력\n(설계·제조·설치·인증·검사·유지관리 등)\n\n✅ **2. 필수 교육 이수**\n한국승강기안전공단에서 실시하는 **'피난용 승강기 관리교육'**")
            return kakao_res([{"basicCard": {"title": "피난용 자격 진단 결과", "description": desc, "buttons": [
                {"action": "webLink", "label": "✍️ 선임 신고하러 가기", "webLinkUrl": "https://minwon.koelsa.or.kr/"},
                {"action": "webLink", "label": "❓ 선임 신청 방법 안내", "webLinkUrl": "https://youtu.be/gtMaxkUw4cc?si=00Rl407_MMy1iNZS"}]}}])

        if "자격요건_다중이용_결과" in utterance:
            desc = ("🏙️ [다중이용 건축물 자격 안내]\n━━━━━━━━━━━━━━\n16층 이상 또는 다중이용시설의 안전관리자는 **기술적인 기본 역량**이 필요합니다.\n\n"
                    "✅ **필수 자격 요건 (중 하나)**\n1️⃣ 승강기ㆍ기계ㆍ전기ㆍ전자 분야 기능사 이상의 자격증\n2️⃣ 승강기ㆍ기계ㆍ전기ㆍ전자 학과나 그 밖에 유사한 전문 학사 졸업 학위\n3️⃣ 6개월 이상의 설계ㆍ제조ㆍ설치ㆍ인증ㆍ검사ㆍ유지관리 경력\n4️⃣한국승강기안전공단에서 실시하는 **'승강기 기술 기본 교육'** 이수\n\n✅ **2. 필수 교육 이수**\n한국승강기안전공단에서 실시하는 **'비상구출운전 승강기관리교육'**")
            return kakao_res([{"basicCard": {"title": "다중이용 자격 진단 결과", "description": desc, "buttons": [
                {"action": "webLink", "label": "✍️ 선임 신고하러 가기", "webLinkUrl": "https://minwon.koelsa.or.kr/"},
                {"action": "webLink", "label": "❓ 선임 신청 방법 안내", "webLinkUrl": "https://youtu.be/gtMaxkUw4cc?si=00Rl407_MMy1iNZS"}]}}])

        if "자격요건_일반_결과" in utterance:
            desc = ("🏠 [일반 건축물 자격 안내]\n━━━━━━━━━━━━━━\n해당 건물은 가장 보편적인 자격 요건이 적용됩니다.\n\n"
                    "✅ **선임 가능 조건**\n1️⃣ 승강기 관리 교육 이수\n2️⃣ 승강기의 제조ㆍ설치 및 유지관리 등에 관한 기술교육 이수/직무 교육 이수\n3️⃣ 안전관리기술자로 하여금 행정안전부장관이 실시하는 승강기 안전관리에 관한 직무교육 이수\n4️⃣ 한국승강기안전공단에서 실시하는 **'승강기 관리 교육'**")
            return kakao_res([{"basicCard": {"title": "일반 자격 진단 결과", "description": desc, "buttons": [
                {"action": "webLink", "label": "✍️ 선임 신고하러 가기", "webLinkUrl": "https://minwon.koelsa.or.kr/"},
                {"action": "webLink", "label": "❓ 선임 신청 방법 안내", "webLinkUrl": "https://youtu.be/gtMaxkUw4cc?si=00Rl407_MMy1iNZS"}]}}])

        # =========================================================
        # 고유번호 7자리 기반 서비스
        # 7자리 고유번호 및 고유번호가 포함된 메뉴 명령은 계산기보다 우선 처리합니다.
        # =========================================================
        if is_elevator_request:
            info = get_info(elv_no)
            cmd = utterance.replace(elv_no, "").strip()

            # =====================================================
            # 👤 안전관리자 현황 조회
            # =====================================================
            if "안전관리자" in cmd:
                root = get_api(URLS['SAFE'], {'serviceKey': KEY, 'elevator_no': elv_no})
                
                # 상단 기본 정보 세팅
                desc = f"✅ [안전관리자 선임 현황]\n건물명: {info['buldNm']}\n━━━━━━━━━━━━━━\n"
                
                if root is not None:
                    items = root.findall('.//item')
                    if items:
                        manager_list = [] # 관리자 여러 명의 정보를 담을 빈 리스트
                        
                        # for문을 사용해 모든 안전관리자 정보를 가져옵니다
                        for idx, m in enumerate(items, 1):
                            name = m.findtext('safeMngrNm') or m.findtext('shuttleMngrNm') or "성함미상"
                            end_de = format_dt(m.findtext('valdEndDt') or m.findtext('eduEndDe'))
                            manager_list.append(f"👤 {idx}. 성함: {name}\n🎓 만료: {end_de}")
                        
                        # 리스트에 모인 정보들을 줄바꿈(\n\n)으로 연결해서 desc에 합칩니다
                        desc += "\n\n".join(manager_list)
                    else:
                        desc += "⚠️ 등록된 안전관리자 정보가 없습니다."
                else:
                    desc += "⚠️ 통신 지연으로 정보를 불러올 수 없습니다."
                
                return kakao_res([{
                    "basicCard": {
                        "title": "⚖️ 법적 의무사항 이행 확인",
                        "description": desc,
                        "buttons": [
                            {"action": "message", "label": "⏰ 선임 기한 안내", "messageText": f"언제까지 선임해야 하나요"},
                            {"action": "message", "label": "🎓 교육 이수 기준", "messageText": f"교육은 언제까지 받나요"},
                            {"action": "message", "label": "📋 안전관리자 직무 조회", "messageText": f"{elv_no} 법정직무조회1"}
                        ]
                    }
                }])
                
            # =====================================================
            # 📜 보험·점검 관련 법령 처음 화면
            # =====================================================
            if "보험및점검" in cmd:
                return kakao_res([{
                    "basicCard": {
                        "title": "📜 보험·점검 관련 법령",
                        "description": "궁금하신 항목을 선택해주세요.",
                        "buttons": [
                            {"action": "message", "label": "🛡️ 보험 가입 필수인가요", "messageText": f"{elv_no} 보험질문"},
                            {"action": "message", "label": "🛠️ 자체점검 필수인가요", "messageText": f"{elv_no} 점검질문"},
                            {"action": "message", "label": " 🏛️공단의 역활 안내", "messageText": f"공단의 역활 안내"}
                        ]
                    }
                }])

            # =========================================================
            # 🛡️ 보험 가입 질문
            # =========================================================
            if "보험질문" in cmd:
                return kakao_res([{
                    "basicCard": {
                        "title": "🛡️ 보험 가입 필수인가요",
                        "description": (
                            "승강기 안전관리법 제30조에 따라 이용자 보호를 위한 보험 가입은 의무 사항입니다.\n"
                            "━━━━━━━━━━━━━━\n"
                            "📍 가입 대상 및 범위\n"
                            "• 승객용·화물용·ES 등 모든 기종\n"
                            "• 휴지(운행중지) 및 불합격 승강기 포함\n"
                            "(사고 발생 시 배상을 위해 예외 없이 가입)"
                        ),
                        "buttons": [
                            {"action": "message", "label": "🔍 보험 가입 여부 조회", "messageText": f"{elv_no} 보험가입확인"},
                            {"action": "message", "label": "↪️ 돌아가기", "messageText": f"{elv_no} 보험및점검"}
                        ]
                    }
                }])

            # =========================================================
            # 🛡️ 보험 가입 조회
            # =========================================================
            if "보험가입" in cmd or "보험확인" in cmd:
                root = get_api(URLS['BULD'], {'serviceKey': KEY, 'elevator_no': elv_no, 'numOfRows': 999})
                    
                if root is not None:
                    items = root.findall('.//item')
                    page = int(re.search(r'페이지(\d+)', cmd).group(1)) if "페이지" in cmd else 1
                        
                    start = (page - 1) * 15
                    display = items[start:start + 15]
                        
                    cards = []
                    for i in range(0, len(display), 3):
                        btns = [{
                            "action": "message",
                            "label": f"{(it.findtext('installationPlace') or '-').strip()}({it.findtext('elevatorNo')})",
                            "messageText": f"{it.findtext('elevatorNo')} 보험결과"
                        } for it in display[i:i+3]]
                            
                        cards.append({
                            "title": f"🛡️ 보험 확인 ({start+i+1}~)",
                            "description": f"🏢 {info['buldNm']}",
                            "buttons": btns
                        })
                            
                    if len(items) > start + 15:
                        cards.append({
                            "title": "🚀 다음 리스트",
                            "buttons": [{
                                "action": "message",
                                "label": "➡️ 다음 보기",
                                "messageText": f"{elv_no} 보험확인 페이지{page+1}"
                            }]
                        })
                            
                return kakao_res([{"carousel": {"type": "basicCard", "items": cards}}])   
                    
            if "보험결과" in cmd:
                return kakao_res([{
                    "basicCard": {
                        "title": "🛡️ 보험 가입 여부 조회",
                        "description": make_insur_report(elv_no, info)
                    }
                }])
                                    
            # =====================================================
            # 🛠️ 자체점검 질문
            # =====================================================            
            if "점검질문" in cmd:
                # 400자 제한에 최적화된 상세 설명 문구
                check_desc = (
                    "🛠️ [승강기 자체점검 안내]\n"
                    "매월 1회 이상 안전 점검은 필수입니다!\n"
                    "━━━━━━━━━━━━━━\n"
                    "🛠️ 월 1회 점검 및 결과 등록\n"
                    "매월 최소 1회 자체점검 후, 결과를 '국가승강기정보센터'에 반드시 입력해야 합니다.\n\n"
                    "✅ 유지관리업체 대행 가능\n"
                    "직접 점검이 어려운 경우 전문업체 대행이 가능합니다.\n\n"
                    "🚨 결함 발견 시 즉시 조치\n"
                    "결함 시 즉시 운행을 중지하고, 보수 완료 전까지 운행해서는 안 됩니다.\n\n"
                    "💡 위반 시 주의사항\n"
                    "미점검 또는 허위 입력 시 과태료 등 행정 처분 대상이 됩니다.")
                
                return kakao_res([
                    {
                        "basicCard": {"title": "🛠️ 자체점검 필수인가요?","description": check_desc,
                                      "buttons": [
                                          {"action": "message","label": "🔍 최근 점검 일지 조회","messageText": f"{elv_no} 자체점검확인"},
                                          {"action": "message","label": "↪️ 돌아가기","messageText": f"{elv_no} 보험및점검"}]}}
                ])
                                    
            # =====================================================
            # 🛠️ 점검 리스트
            # =====================================================            
            if "자체점검" in cmd:
                root = get_api(URLS['BULD'], {'serviceKey': KEY, 'elevator_no': elv_no, 'numOfRows': 999})
                    
                if root is not None:
                    items = root.findall('.//item')
                    page = int(re.search(r'페이지(\d+)', cmd).group(1)) if "페이지" in cmd else 1
                        
                    start = (page - 1) * 15
                    display = items[start:start + 15]
                        
                    cards = []
                    for i in range(0, len(display), 3):
                        btns = [{
                            "action": "message",
                            "label": f"{(it.findtext('installationPlace') or '-').strip()}({it.findtext('elevatorNo')})",
                            "messageText": f"{it.findtext('elevatorNo')} 결과확인"
                        } for it in display[i:i+3]]
                        
                        cards.append({
                            "title": f"🛠️ 점검 확인 ({start+i+1}~)",
                            "description": f"🏢 {info['buldNm']}",
                            "buttons": btns
                        })
                        
                    if len(items) > start + 15:
                        cards.append({
                            "title": "🚀 다음 리스트",
                            "buttons": [{
                                "action": "message",
                                "label": "➡️ 다음 보기",
                                "messageText": f"{elv_no} 자체점검 페이지{page+1}"
                            }]
                        })
                        
                return kakao_res([{"carousel": {"type": "basicCard", "items": cards}}])
                
            if "결과확인" in cmd:
                return kakao_res([{
                    "basicCard": {
                        "title": "🔍 최근 점검 일지 조회",
                        "description": make_check_report(elv_no, info) 
                    }
                }])
                
        # =========================================================
        # ⚙️ 호기조회 (고유번호 뒤에 무조건, 삭제 금지)
        # =========================================================
            if "호기정보" in utterance:
                root = get_api(URLS['BULD'], {'serviceKey': KEY, 'elevator_no': elv_no, 'numOfRows': 999})
                if root is not None:
                    items = root.findall('.//item')
                    page = int(re.search(r'페이지(\d+)', utterance).group(1)) if "페이지" in utterance else 1
                    start = (page-1)*15
                    display = items[start:start+15]
                    cards = []
                    for i in range(0, len(display), 3):
                        sub = display[i:i+3]
                        btns = [{"action": "message", "label": f"{(it.findtext('installationPlace') or '-').strip()}({it.findtext('elevatorNo')})", "messageText": f"{it.findtext('elevatorNo')} 조회제원"} for it in sub]
                        cards.append({"title": f"🔢 리스트 ({start+i+1}~)", "description": f"🏢 {info['buldNm']}", "buttons": btns})
                    if len(items) > start+15: cards.append({"title": "🚀 다음 리스트", "buttons": [{"action": "message", "label": "➡️ 다음 15개 보기", "messageText": f"{elv_no} 호기정보 페이지{page+1}"}]})
                    return kakao_res([{"carousel": {"type": "basicCard", "items": cards}}])
                    
            if "조회제원" in utterance:
                spec_text = make_spec_report(elv_no, info)
                
                return kakao_res([{
                    "basicCard": {
                        "title": f"⚙️ {elv_no} 호기 상세제원",
                        "description": spec_text,
                        "buttons": [
                            {"action": "message","label": "📅 검사 적용 시점 확인","messageText": f"연도계산"} # 명칭 수정
                        ]
                    }
                }])

        # =========================================================
        # 안전관리자 직무 조회
        # =========================================================
            
            check_titles = ["", "기계실/제어반 잠금 확인", "기계실 온도/환기 확인", "버튼 작동상태 확인", "법정 부착물 확인", "비상통화장치 확인", "비상열쇠 보관 확인"]
            check_descs = ["", 
                "기계실 또는 제어반 잠금 상태\n\n기계실이 있는 경우 출입문 잠금을 확인하세요. MRL은 제어반 닫힘 상태를 확인합니다.",
                "기계실 온도 및 환기장치\n\n기계실 온도는 40도 이하를 유지해야 합니다.",
                "호출 및 등록버튼 작동 상태\n\n내외부 버튼 파손 및 작동 여부를 확인하세요.",
                "표준부착물 부착 상태\n\n검사합격증과 고유번호 7자리 훼손 여부를 확인하세요.",
                "비상통화장치 작동 상태\n\n갇힘 사고 시 외부와 연락 가능한지 체크하세요.",
                "비상열쇠 관리 상태\n\n지정된 위치에 비상열쇠가 보관 중인지 확인하세요."]
            bad_descs = ["",
                "🚨 기계실/제어반이 열려 있습니다! 즉시 잠금 조치하세요.",
                "🚨 기계실 온도가 높거나 환기가 안 됩니다! 환기 장치를 가동하세요.",
                "🚨 버튼 작동 불량입니다! 유지보수 업체에 수리를 요청하세요.",
                "🚨 합격증 또는 고유번호 훼손! 즉시 재부착하세요.",
                "🚨 비상통화 불능! 즉각 수리가 필요합니다.",
                "🚨 비상열쇠 부재! 지정 위치에 반드시 비치하세요."]

            for i in range(1, 7):
                if f"법정직무조회{i}" in utterance:
                    next_idx = i + 1
                    next_text = f"법정직무조회{next_idx}" if next_idx <= 6 else "법정직무조회7"
                    return kakao_res([{"basicCard": {"title": check_titles[i], "description": check_descs[i], "buttons": [
                        {"action": "message", "label": "✅ 양호", "messageText": f"{elv_no} {next_text}"},
                        {"action": "message", "label": "❌ 불량", "messageText": f"{elv_no} 법정직무불량{i}"}]}}])

            for i in range(1, 7):
                if f"법정직무불량{i}" in utterance:
                    next_text = f"법정직무조회{i+1}" if i+1 <= 6 else "법정직무조회7"
                    return kakao_res([{"basicCard": {"title": f"⚠️ {i}번 불량 안내", "description": bad_descs[i], "buttons": [
                        {"action": "message", "label": "🛠️ 조치 완료 (양호)", "messageText": f"{elv_no} {next_text}"}]}}])

            if "법정직무조회7" in utterance:
                root = get_api(URLS['BULD'], {'serviceKey': KEY, 'elevator_no': elv_no, 'numOfRows': 999})
                if root is not None:
                    items = root.findall('.//item')
                    page = int(re.search(r'페이지(\d+)', utterance).group(1)) if "페이지" in utterance else 1
                    start = (page-1)*15
                    display = items[start:start+15]
                    cards = []
                    for i in range(0, len(display), 3):
                        btns = [{"action": "message", "label": f"{(it.findtext('installationPlace') or '-').strip()}({it.findtext('elevatorNo')})", "messageText": f"{it.findtext('elevatorNo')} 직무보험"} for it in display[i:i+3]]
                        cards.append({"title": f"🛡️ 보험 확인 ({start+i+1}~)", "description": f"🏢 {info['buldNm']}", "buttons": btns})

                     # 다음 페이지가 있을 경우 추가
                    if len(items) > start+15: cards.append({"title": "🚀 다음 리스트", "buttons": [{"action": "message", "label": "➡️ 다음 보기", "messageText": f"{elv_no} 법정직무조회7 페이지{page+1}"}]})
                    return kakao_res([{"carousel": {"type": "basicCard", "items": cards}}])

            if "직무보험" in utterance:
                return kakao_res([{"basicCard": {"title": "🛡️ 보험 가입 여부 확인", "description": make_insur_report(elv_no, info), "buttons": [
                    {"action": "message", "label": "🔍 최근 점검 일지 조회", "messageText": f"{elv_no} 법정직무조회8"}]}}]) # 🔍 자체점검일지 확인 -> 명칭수정
                
            if "법정직무조회8" in utterance:
                root = get_api(URLS['BULD'], {'serviceKey': KEY, 'elevator_no': elv_no, 'numOfRows': 999})
                if root is not None:
                    items = root.findall('.//item')
                    page = int(re.search(r'페이지(\d+)', utterance).group(1)) if "페이지" in utterance else 1
                    start = (page - 1) * 15
                    display = items[start:start + 15]
                    cards = []
                    
                    for i in range(0, len(display), 3):
                        sub_items = display[i:i + 3]
                        btns = [{"action": "message","label": f"{(it.findtext('installationPlace') or '-').strip()}({it.findtext('elevatorNo')})","messageText": f"{it.findtext('elevatorNo')} 직무점검"} for it in sub_items]
                        cards.append({"title": f"📝 점검 확인 ({start + i + 1}~)","description": f"🏢 {info['buldNm']}","buttons": btns})
                        
                    # 다음 페이지가 있을 경우 추가
                    if len(items) > start + 15:cards.append({"title": "🚀 다음 리스트","buttons": [{"action": "message","label": "➡️ 다음 보기","messageText": f"{elv_no} 법정직무조회8 페이지{page + 1}"}]})
                    return kakao_res([{"carousel": {"type": "basicCard", "items": cards}}])

            if "직무점검" in utterance:
                selected_info = get_info(elv_no)
                return kakao_res([{"basicCard": {"title": "🔍 최근 점검 일지 조회","description": make_check_report(elv_no, selected_info),"buttons": [
                    {"action": "message", "label": "🏁 모든 확인 완료", "messageText": f"{elv_no} 최종마무리"}]}}])
                
            if "최종마무리" in utterance:
                return kakao_res([{"basicCard": {"title": "✨ 수고하셨습니다!","description": f"건물({info['buldNm']})의 모든 확인을 마쳤습니다. 👍\n\n지금까지의 체크 리스트는 참고하시어 주기적으로 확인 하시기 바랍니다. 😄",
                        "buttons": [{"action": "message","label": "처음으로 돌아가기","messageText": f"{elv_no}"}]}}])
                
        # =========================================================
        # 메인 베이스 화면 (아래 수정 금지 구역 - 라벨명만 구조도 반영)
        # =========================================================
            return kakao_res([{"basicCard": {"title": f"🏢 {info['buldNm']}", "description": f"📍 주소: {info['addr']}", "buttons": [
                {"action": "message", "label": "👤 안전관리자 현황 조회", "messageText": f"{elv_no} 👤 안전관리자 현황 조회"},
                {"action": "message", "label": "📜 보험·점검 관련 법령", "messageText": f"{elv_no} 보험및점검"}
            ]}}])
                #"action": "message", "label": "🚨 사고·고장 이력", "messageText": f"{elv_no} 🚨 사고/고장 신고조회" 추후 업데이트 할것


        return kakao_res([{"simpleText": {"text": "❓ 고유번호 7자리를 입력해주세요."}}])
    except Exception as e:
        return kakao_res([{"simpleText": {"text": f"⚠️ 서버 오류: {str(e)}"}}])

@app.route('/ask', methods=['POST'])
def ask():
    content = request.get_json(silent=True)
    if not isinstance(content, dict) or not isinstance(content.get('userRequest'), dict):
        return jsonify({'version': '2.0', 'template': {'outputs': [{'simpleText': {'text': '요청 형식을 확인해 주세요.'}}]}})
    try:
        reply, routed = bridge.handle(content)
    except Exception:
        app.logger.exception('request routing failed')
        return kakao_res([{'simpleText': {'text': '요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.'}}])
    if reply is not None:
        return jsonify(reply)
    g.routed_utterance = routed
    return legacy_ask()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
assistant_bridge.py
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
                'measurement': '측정 기준 확인', 'inspection': '검사 항목 안내'}
TOPIC_BUTTONS = {
    '카 상부': [('카 상부 난간', 'detail'), ('상부틈새', 'measurement'), ('피난공간', 'detail')],
    '피트': [('피트 사다리', 'detail'), ('하부틈새', 'measurement'), ('피트 정지장치', 'detail')],
    '기계실': [('기계실 출입', 'detail'), ('기계실 작업공간', 'detail'), ('기계실 조명', 'detail')],
    '승강장': [('승강장 문', 'detail'), ('승강장 잠금장치', 'detail'), ('승강장 조명', 'detail')],
    '카': [('카 구조', 'detail'), ('카 내부 설비', 'detail'), ('카 상부', 'detail')],
}
ALIASES = {
    '피트난간': ['피트', '난간', '칸막이', '3.1.3(13)', '5.6', '6.5.5'],
    '피트단차': ['피트', '난간', '칸막이', '3.1.3(13)'],
    '피트이스탑': ['피트', '정지장치', '정지스위치'],
    '기계실사다리': ['기계실', '출입 통로', '6.2.5'],
}
# 표준화는 배포 자료에 포함하지 않고 검색 단계에서도 차단한다.
ALLOWED_CATEGORIES = {'law', 'addenda', 'inspection'}

def compact(s):
    return re.sub(r'\s+', '', s).lower()

def card(title, body, buttons=()):
    return {'basicCard': {'title': title, 'description': body,
                          'buttons': list(buttons), 'buttonLayout': 'vertical'}}

def button(label, message):
    return {'action': 'message', 'label': label[:14], 'messageText': message}

def envelope(outputs):
    return {'version': '2.0', 'template': {'outputs': outputs}}

def notice(text, buttons=()):
    return envelope([card('승강기 검사 도우미', text, buttons)])

def calculation_note(states, user):
    step = states.get(user, {}).get('step')
    return {
        'max_runby_input': '최대 런바이 입력 대기 중입니다. 숫자 6개를 입력하면 계산을 이어갑니다.',
        'other1_input': '피난공간 높이 입력 대기 중입니다. 측정값(m)을 입력하면 계산을 이어갑니다.',
        'other2_input': '파이널 리미트 캠 입력 대기 중입니다. 측정값(mm)을 입력하면 계산을 이어갑니다.',
    }.get(step, '')

class Corpus:
    """전 자료 검색 후 위치를 반환. 검색 결과와 원문 열람을 구분한다."""
    def __init__(self):
        self.sources = {x['id']: x for x in json.loads((BASE/'catalog.json').read_text())
                        if x['category'] in ALLOWED_CATEGORIES}
        self.pages = json.loads((BASE/'search_pages.json').read_text())
        self.pages = [p for p in self.pages if p['source'] in self.sources]
        self.lookup = {f"{p['source']}:{p['page']}": p for p in self.pages}

    def search(self, terms, offset=0):
        terms = [str(x)[:120] for x in terms[:20] if compact(str(x))]
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
            sid = p['source']; src = self.sources[sid]
            lines = p['text'].splitlines()
            excerpts = [line.strip() for line in lines if any(n in compact(line) for n in needles)]
            hits.append((score, {'ref': f"{sid}:{p['page']}", 'filename': src['filename'],
                                 'category': src['category'], 'excerpt': ' / '.join(excerpts)[:650]}))
        hits.sort(key=lambda x: (-x[0], x[1]['ref']))
        offset = max(0, int(offset))
        return {'total': len(hits), 'offset': offset, 'next_offset': offset+30 if offset+30<len(hits) else None,
                'matches': [h[1] for h in hits[offset:offset+30]],
                'warning': '검색 일치는 검토 완료가 아닙니다. 관련 원문과 참조 조항을 열람하세요.'}

    def read(self, refs, visual=False):
        result, images = [], []
        for ref in refs[:5]:
            p = self.lookup.get(ref)
            if p is None:
                continue
            src = self.sources[p['source']]
            result.append({'ref': ref, 'filename': src['filename'], 'text': p['text']})
            if visual:
                import fitz
                with fitz.open(BASE/src['path']) as pdf:
                    pix = pdf[p['page']-1].get_pixmap(matrix=fitz.Matrix(1.4, 1.4))
                    images.append({'type': 'input_image', 'image_url': 'data:image/png;base64,'+
                                   base64.b64encode(pix.tobytes('png')).decode(), 'detail': 'high'})
        return result, images

TOOLS = [
    {'type':'function', 'name':'search_documents', 'description':'제공 법령·부칙·검사기준 전체 검색. 종전 용어도 함께 검색한다.',
     'parameters':{'type':'object','properties':{'terms':{'type':'array','items':{'type':'string'}},'offset':{'type':'integer'}},'required':['terms','offset'],'additionalProperties':False},'strict':True},
    {'type':'function', 'name':'read_pages', 'description':'검색된 ref의 원문 열람. 부속서·그림·표·수치가 있으면 visual=true로 원본도 확인.',
     'parameters':{'type':'object','properties':{'refs':{'type':'array','items':{'type':'string'}},'visual':{'type':'boolean'}},'required':['refs','visual'],'additionalProperties':False},'strict':True},
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
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self.corpus = None

    def clear(self, user):
        with self.lock:
            self.states.pop(user, None)
            self.sessions.pop(user, None)
            for k in [k for k,v in self.jobs.items() if v['user']==user]:
                self.jobs.pop(k, None)

    def menu_buttons(self, topic):
        pairs = TOPIC_BUTTONS.get(topic)
        if pairs:
            return [button(t+' 기준', '[기준조회] '+t+' | '+a) for t,a in pairs]
        return [button(ACTION_NAMES[a], '[기준조회] '+topic+' | '+a)
                for a in ('detail','application','history')]

    def attach_note(self, reply, user):
        note = calculation_note(self.states, user)
        if note:
            out = reply['template']['outputs'][-1]
            if 'basicCard' in out:
                body = out['basicCard']['description']
                if len(body+'\n\n'+note) <= 400:
                    out['basicCard']['description'] = body+'\n\n'+note
        return reply

    def handle(self, content):
        """즉시 답변과 기존 함수로 전달할 대체 발화를 반환한다."""
        request = content.get('userRequest') or {}
        user = (request.get('user') or {}).get('id')
        raw = str(request.get('utterance') or '').strip()
        word = compact(raw)
        if not user:
            return notice('사용자 식별정보를 확인하지 못했습니다. 다시 시도해 주세요.'), None
        if len(raw)>2000:
            return notice('질문을 2,000자 이내로 입력해 주세요.'), None
        if word in RESET:
            self.clear(user)
            outputs=[]
            image=os.getenv('START_IMAGE_URL','')
            if image.startswith('https://'):
                outputs.append({'simpleImage':{'imageUrl':image,'altText':'서울서부지사 챗봇 시작'}})
            outputs.append(card('승강기 검사 통합 도우미', '승강기 고유번호 7자리 또는 궁금한 검사기준을 입력해 주세요.\n최대 런바이: 최대런바이\n정밀검사 연도: 설치 연도 4자리\n입력 대기와 저장값을 초기화했습니다.'))
            return envelope(outputs), None
        if raw.startswith('[답변확인] '):
            return self.poll(raw.split(' ',1)[1],user), None
        session=self.sessions.setdefault(user,{})
        if re.match(r'^\d{7}(?!\d)',raw):
            self.states.pop(user,None)  # 새 고유번호 조회는 계산 대기·저장값 전부 초기화
            pending=session.pop('pending',None)
            if re.fullmatch(r'\d{7}',raw) and pending:
                raw += ' '+ {'selfcheck':'자체점검','insurance':'보험가입확인'}[pending]
            session['last_id']=raw[:7]
            return None,raw
        if word in LEGACY or '최대런바이' in word or '자격요건_' in word or '선임자격' in word or '자격진단' in word:
            session.pop('pending', None)
            return None,None
        numeric=all(re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?',x) for x in raw.split()) if raw else False
        if numeric:
            if session.get('pending'):
                return notice('승강기 고유번호 7자리를 입력해 주세요.'),None
            if not calculation_note(self.states,user) and len(raw.split())==6:
                return notice('최대런바이를 입력해 계산을 먼저 시작해 주세요.',[button('최대 런바이 계산','최대런바이')]),None
            return None,None  # 4자리 및 계산 후속 숫자는 기존 처리
        service=None
        if '자체점검' in word and any(x in word for x in ('조회','일지','결과','확인')) and not any(x in word for x in ('기준','의무','법령')):
            service='selfcheck'
        elif '보험' in word and any(x in word for x in ('조회','가입','확인')) and not any(x in word for x in ('의무','기준','해야','필수','법령')):
            service='insurance'
        if service:
            session['pending']=service
            name='자체점검 결과' if service=='selfcheck' else '보험 가입 여부'
            return self.attach_note(notice(name+'를 조회할 승강기 고유번호 7자리를 입력해 주세요.'),user),None
        if word=='보험':
            return self.attach_note(notice('어떤 보험 내용을 확인하시겠어요?',[
                button('보험 가입 여부 조회','보험 조회'),button('보험 가입 의무 기준','보험 가입 의무 기준')]),user),None
        if not os.getenv('OPENAI_API_KEY'):
            return self.attach_note(notice('기준 검색 연결이 아직 설정되지 않았습니다. 기존 고유번호 조회와 계산은 사용할 수 있습니다.'),user),None
        with self.lock:
            now=time.time()
            for k in [k for k,v in self.jobs.items() if now-v['created']>900]:
                self.jobs.pop(k,None)
            if len(self.jobs)>=50 or sum(1 for j in self.jobs.values() if not j['future'].done())>=4:
                return self.attach_note(notice('검색 요청이 많습니다. 잠시 후 다시 시도해 주세요.'),user),None
            jid=uuid.uuid4().hex
            future=self.pool.submit(self.answer,raw,session.get('topic',''))
            self.jobs[jid]={'user':user,'created':now,'future':future}
        return self.attach_note(notice('관련 법령과 적용 조건을 확인하고 있습니다. 잠시 후 답변 확인을 눌러 주세요.',[button('답변 확인','[답변확인] '+jid)]),user),None

    def poll(self,jid,user):
        job=self.jobs.get(jid)
        if not job or job['user']!=user:
            return notice('조회가 만료되었거나 초기화되었습니다. 질문을 다시 입력해 주세요.')
        future=job['future']
        if not future.done():
            return self.attach_note(notice('관련 자료를 확인 중입니다. 잠시 후 다시 눌러 주세요.',[button('답변 확인','[답변확인] '+jid)]),user)
        try:
            result=future.result()
        except Exception:
            logging.exception('standards lookup failed')
            return self.attach_note(notice('자료 검색 또는 답변 생성을 완료하지 못했습니다. 잠시 후 질문을 다시 입력해 주세요.'),user)
        kind=result.get('kind')
        if kind in ('selfcheck','insurance'):
            self.sessions.setdefault(user,{})['pending']=kind
            return self.attach_note(notice('조회할 승강기 고유번호 7자리를 입력해 주세요.'),user)
        if kind=='runby':
            return notice('최대런바이를 입력하면 기존 계산을 시작합니다.',[button('최대 런바이 계산','최대런바이')])
        topic=result.get('topic') or '승강기 기준'
        self.sessions.setdefault(user,{})['topic']=topic
        outputs=[]
        for c in result['cards']:
            outputs.append(card(c['title'],c['body'],self.menu_buttons(c.get('topic') or topic)))
        return self.attach_note(envelope(outputs),user)

    def answer(self,question,previous_topic):
        if self.corpus is None:
            self.corpus=Corpus()
        inputs=[{'role':'user','content':json.dumps({'question':question,'previous_topic':previous_topic,
                 'sources':[{'id':k,'filename':v['filename'],'category':v['category'],'pages':v['pages']} for k,v in self.corpus.sources.items()]},ensure_ascii=False)}]
        seen=set();searched=False
        for turn in range(8):
            payload={'model':os.getenv('OPENAI_MODEL','gpt-6.1-sol'), 'instructions':INSTRUCTIONS,
                     'input':inputs,'tools':TOOLS,'max_output_tokens':3500,'store':False}
            response=requests.post('https://api.openai.com/v1/responses',headers={
                'Authorization':'Bearer '+os.environ['OPENAI_API_KEY'],'Content-Type':'application/json'},json=payload,timeout=(5,60))
            response.raise_for_status(); data=response.json()
            if data.get('status')!='completed':
                raise RuntimeError('incomplete API response')
            logging.info('openai usage=%s',data.get('usage',{}))
            items=data.get('output',[]);inputs.extend(items)
            calls=[x for x in items if x.get('type')=='function_call']
            if calls:
                for call in calls[:6]:
                    args=json.loads(call['arguments']);images=[]
                    if call['name']=='search_documents':
                        result=self.corpus.search(args['terms'],args['offset']);searched=True
                    elif call['name']=='read_pages':
                        result,images=self.corpus.read(args['refs'],args['visual'])
                        seen.update(p['ref'] for p in result)
                    else:result={'error':'unsupported tool'}
                    inputs.append({'type':'function_call_output','call_id':call['call_id'],'output':json.dumps(result,ensure_ascii=False)})
                    if images:
                        inputs.append({'role':'user','content':[{'type':'input_text','text':'직전 read_pages에 대응하는 원본 페이지 그림입니다.'}]+images})
                continue
            text=''.join(p.get('text','') for x in items if x.get('type')=='message' for p in x.get('content',[]) if p.get('type')=='output_text')
            result=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip()))
            if result.get('kind') in ('selfcheck','insurance','runby'):
                return result
            cards=result.get('cards',[])
            if not 1<=len(cards)<=3:raise ValueError('card count')
            for c in cards:
                refs=c.get('refs',[])
                if result.get('kind')=='answer' and (not searched or not refs or not set(refs)<=seen):
                    raise ValueError('unread citation')
                if any(r not in seen for r in refs):raise ValueError('invalid citation')
                if len(c['body'])>280 or len(c['title'])>40:raise ValueError('card too long')
                sources=[]
                for ref in refs:
                    sid,page=ref.split(':');name=self.corpus.sources[sid]['filename']
                    short=re.sub(r'\(.*?\)','',name).replace('.pdf','')
                    sources.append(short[:55]+' '+page+'쪽')
                c['body']+= ('\n근거: '+' / '.join(sources)) if sources else ''
                if len(c['body'])>320:raise ValueError('citation overflow')
            return result
        raise RuntimeError('검색 검토 회수 한도에 도달했습니다. 전수검토 완료로 처리하지 않습니다.')
