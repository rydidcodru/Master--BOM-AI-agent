import os
import re
import openpyxl
import pandas as pd
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Import existing modules
from ppt_parser import parse_pptx_file
from bom_tree import parse_bom_excel, InMemoryBOMRepository
from query_agent import process_change_request, match_target_node_with_llm

# Directory Paths
AGENT_DIR = Path("/Users/hyeonu/workspace/Project/Master--BOM-AI-agent/agent")
COMPACT_OVEN_DIR = AGENT_DIR / "Compact Oven"

ppt_path = COMPACT_OVEN_DIR / "NXI 개발 유형 및 등급 확정 심의회 Compact Oven 0404 (1) (1).pptx"
base_bom_path = COMPACT_OVEN_DIR / "WSED7613S.ASTQEUR@CVZ.EKHQ 1.0 (1)(base_bom).xlsx"
master_path = COMPACT_OVEN_DIR / "통합 개발부품Master Compact v1.1.xlsx"


class CompactOvenProcessor:
    def __init__(self):
        self.ppt_data = {}
        self.bom_repo = None
        self.master_df = None
        self.master_rows = []

    def load_inputs(self) -> None:
        """PPT, Base BOM 및 Master 엑셀 입력을 로드합니다."""
        print("[1/5] 입력 데이터 로드 중...")
        # 1. Parse PPT
        print(f"  - PPT 파일 파싱: {ppt_path.name}")
        self.ppt_data = parse_pptx_file(ppt_path)
        print(f"    * PPT 상세 변경 건수: {len(self.ppt_data.get('detailed_changes', []))}")
        print(f"    * PPT 모듈 상세 건수: {len(self.ppt_data.get('module_details', []))}")

        # 2. Parse Base BOM and Build NetworkX Tree
        print(f"  - Base BOM 트리 구축: {base_bom_path.name}")
        self.bom_repo = InMemoryBOMRepository()
        nodes = parse_bom_excel(base_bom_path)
        for n in nodes:
            self.bom_repo.save_node(n)
        for n in nodes:
            if n.parent_part_no:
                self.bom_repo.save_relationship(n.parent_part_no, n.part_no)
        print(f"    * Base BOM 노드 수: {len(nodes)}")

        # 3. Load Master Sheet
        print(f"  - Master 부품 리스트 로드: {master_path.name}")
        self.master_df = pd.read_excel(master_path, sheet_name="Master", header=None)
        print(f"    * Master 행 수: {self.master_df.shape[0]}")

    def build_master_hierarchy(self) -> None:
        """Master 시트의 레벨 구조를 분석하여 부모-자식 관계 트리를 구축합니다."""
        print("[2/5] Master 시트 계층 구조 분석 중...")
        self.master_rows = []
        stack = [] # (depth, index_in_master_rows)

        # Row 9 (Excel index 8)부터 데이터 시작
        for idx in range(9, len(self.master_df)):
            row = self.master_df.iloc[idx]
            no = row[1]
            lvl_raw = str(row[2]).strip() if pd.notna(row[2]) else ''
            part_type = str(row[3]).strip() if pd.notna(row[3]) else ''
            base_pno = str(row[4]).strip() if pd.notna(row[4]) else ''
            new_pno = str(row[5]).strip() if pd.notna(row[5]) else ''
            part_name = str(row[6]).strip() if pd.notna(row[6]) else ''
            change_point = str(row[9]).strip() if pd.notna(row[9]) else '-'
            change_reason = str(row[10]).strip() if pd.notna(row[10]) else '-'
            classification = str(row[12]).strip() if pd.notna(row[12]) else ''

            if not lvl_raw:
                continue

            # Calculate Depth
            dot_count = len(lvl_raw) - len(lvl_raw.lstrip("."))
            if dot_count > 0:
                lvl_depth = dot_count
            else:
                try:
                    lvl_depth = int(float(lvl_raw))
                except ValueError:
                    lvl_depth = 1

            item = {
                'row_num': idx + 1,  # 1-indexed Excel Row
                'no': no,
                'lvl_raw': lvl_raw,
                'lvl_depth': lvl_depth,
                'part_type': part_type,
                'base_pno': base_pno,
                'new_pno': new_pno,
                'part_name': part_name,
                'change_point': change_point,
                'change_reason': change_reason,
                'classification': classification,
                'parent_idx': None,
                'children_idx': [],
                'is_changed': False, # 단품 매칭된 핵심 변경 노드 여부
                'refined_change_point': None,
                'refined_change_reason': None,
                'refined_new_pno': None,
                'refined_classification': None
            }
            self.master_rows.append(item)

        # Build Parent-Child links
        for i, r in enumerate(self.master_rows):
            depth = r['lvl_depth']
            stack = [item for item in stack if item[0] < depth]
            if stack:
                parent_pos = stack[-1][1]
                r['parent_idx'] = parent_pos
                self.master_rows[parent_pos]['children_idx'].append(i)
            stack.append((depth, i))

        print(f"    * 복원된 Master 트리 노드 수: {len(self.master_rows)}")

    def match_changes(self) -> None:
        """PPT 변경 상세 정보와 마스터 단품을 규칙 및 LLM 기반으로 매칭합니다."""
        print("[3/5] PPT 사양 및 변경 사유 매칭 중...")

        # 1. PPT 상세 변경점 병합 및 정리
        # PPT 슬라이드 7, 8, 9, 11~16 등에서 파싱된 정보들을 병합
        detailed = self.ppt_data.get('detailed_changes', [])
        mod_details = self.ppt_data.get('module_details', [])
        
        # 특정 모듈/부품 번호 매핑 룰 정의
        # PPT에서 파싱된 TBD 품번들에 대한 매칭 및 사유 세팅
        rules = [
            # --- Cavity ---
            {
                "keywords": ["Cavity Assembly,Coating", "Cavity Assembly,welding"],
                "change_point": "구조 변경",
                "change_reason": "제품 치수 변경에 따른 H 치수 137mm 감소 및 조립 방식 변경 (MWO, Steam 기능 착탈 구조 반영)",
                "new_pno": "TBD"
            },
            {
                "keywords": ["Plate,U Bending(Lower)", "Plate,Flat(Lower)"],
                "change_point": "사이즈 축소",
                "change_reason": "제품 치수 축소에 따른 하부 플레이트 길이 축소(1,170→890mm)",
                "new_pno": "←"
            },
            {
                "keywords": ["Plate,Upper"],
                "change_point": "구조 변경",
                "change_reason": "후면구조 변경 및 MWO 차폐 구조 적용으로 후면 공간 확보",
                "new_pno": "←"
            },
            {
                "keywords": ["Plate Assembly,Rear", "Plate,Rear"],
                "change_point": "사이즈 축소",
                "change_reason": "제품 높이 감소에 따른 후면 플레이트 높이 축소(400.5→271.7mm)",
                "new_pno": "←"
            },
            {
                "keywords": ["Heater,Conv", "Heater,Convection"],
                "change_point": "구조 변경",
                "change_reason": "Conv Heater 삭제에 따른 인쇄 및 조립 위치 제거 (Z밴딩 삭제 및 높이 변경)",
                "new_pno": "←"
            },
            {
                "keywords": ["Heater,Sheath"],
                "change_point": "Spec 변경",
                "change_reason": "Heater Spec 변경(2000W → 950W) 및 높이 변경",
                "new_pno": "←"
            },
            {
                "keywords": ["Bracket,Heater"],
                "change_point": "부품 추가",
                "change_reason": "Heater 체결용 BKT부품 신규 설계 반영",
                "new_pno": "TBD"
            },
            {
                "keywords": ["Heater,Halogen"],
                "change_point": "사이즈 축소",
                "change_reason": "길이 변경(2300→2010mm)",
                "new_pno": "←"
            },
            {
                "keywords": ["Gasket,Cavity"],
                "change_point": "사이즈 축소",
                "change_reason": "Cavity 높이 감소(137mm)에 따른 가스켓 안착부 포밍 및 Gasket 사이즈 축소",
                "new_pno": "←"
            },
            {
                "keywords": ["Motor Assembly,Convection", "Motor,BLDC", "Motor,Convection"],
                "change_point": "부품 변경",
                "change_reason": "BLDC Conv. Fan Motor 신규 적용 (AC → BLDC, 30인치 SKS 공용)",
                "new_pno": "TBD"
            },
            {
                "keywords": ["Fan,Convection", "Blade"],
                "change_point": "부품 변경",
                "change_reason": "Fan 크기 변경(153→105.6mm) 및 신규 Fan Blade 적용 (30인치 SKS 공용)",
                "new_pno": "TBD"
            },
            {
                "keywords": ["Cover Assembly,Fan", "Cover,Fan"],
                "change_point": "구조 변경",
                "change_reason": "Fan Cover 금형 수정 및 Fan 모듈 적용 구조 반영",
                "new_pno": "←"
            },
            {
                "keywords": ["LED", "Lamp,LED"],
                "change_point": "부품 추가",
                "change_reason": "Side LED 적용 구조 추가에 따른 램프 신규 적용",
                "new_pno": "TBD"
            },

            # --- Door ---
            {
                "keywords": ["Door Assembly,Full"],
                "change_point": "구조 변경",
                "change_reason": "신규 Platform으로 인한 높이 137.0mm 감소 및 MWO 차폐 구조 반영",
                "new_pno": "TBD"
            },
            {
                "keywords": ["Window Assembly", "Window,Glass"],
                "change_point": "사이즈 축소",
                "change_reason": "Door 높이 137mm 감소에 따른 Glass 크기 축소 및 인쇄 사양 변경",
                "new_pno": "TBD"
            },
            {
                "keywords": ["Frame,Door", "Frame Assembly,Door"],
                "change_point": "구조 변경",
                "change_reason": "높이 137.0mm 감소 및 전자레인지 누설 차폐를 위한 구조 반영",
                "new_pno": "TBD"
            },
            {
                "keywords": ["Panel,Door"],
                "change_point": "구조 변경",
                "change_reason": "Frame Assembly와 Hinge Assembly 체결용 구조 및 Outer Glass Sealing 구조 반영",
                "new_pno": "←"
            },
            {
                "keywords": ["Hinge Assembly", "Hinge"],
                "change_point": "부품 변경",
                "change_reason": "Door 무게 변경(9.2→5.5kg)에 따른 Spring 강도 강하 및 Hinge Bar 형상 수정(Soft Closing)",
                "new_pno": "TBD"
            },
            {
                "keywords": ["Choke,Cover", "Cover,Choke"],
                "change_point": "구조 변경",
                "change_reason": "누설 차폐에 중요한 Frame Choke 보호 구조 반영",
                "new_pno": "←"
            },
            {
                "keywords": ["Camera"],
                "change_point": "부품 추가",
                "change_reason": "Camera Module(LED) 장착 구조 반영 및 AI 기능 연계",
                "new_pno": "TBD"
            },

            # --- Controller ---
            {
                "keywords": ["Controller Assembly"],
                "change_point": "구조 변경",
                "change_reason": "6.8인치 Wide LCD 적용, Matt Black 외관 적용 및 UI/UX 변경에 따른 구조 변경",
                "new_pno": "TBD"
            },
            {
                "keywords": ["Display", "LCD"],
                "change_point": "부품 변경",
                "change_reason": "6.8” Wide LCD 적용 및 Matt Black 외관 적용",
                "new_pno": "TBD"
            },
            {
                "keywords": ["Panel Control", "Panel,Control"],
                "change_point": "구조 변경",
                "change_reason": "6.8인치 LCD 적용 Panel Control 신작 및 DQA1 PCB 조립 구조 추가",
                "new_pno": "TBD"
            },
            {
                "keywords": ["PCB", "DQA1", "DQ1"],
                "change_point": "부품 추가",
                "change_reason": "AI (DQ1) PCB 및 DQA1 PCB 조립 구조 신규 설계 반영 (Wi-Fi, Camera)",
                "new_pno": "TBD"
            },

            # --- Insulator ---
            {
                "keywords": ["Insulator Assembly"],
                "change_point": "구조 변경",
                "change_reason": "Inverter, Fan, PCB, Water Tank 등 각종 부품 조립 및 안착 구조 반영",
                "new_pno": "TBD"
            },
            {
                "keywords": ["Insulator"],
                "change_point": "구조 변경",
                "change_reason": "Inverter, Fan, PCB, Water Tank 등 각종 부품 Assembly 조립 구조 반영 및 MWO 부품 안착 구조 반영",
                "new_pno": "←"
            },
            {
                "keywords": ["Duct,Cooling", "Duct"],
                "change_point": "구조 변경",
                "change_reason": "MGT, Inverter Cooling 구조 Duct 적용 및 MWO 기능 추가에 따른 Cooling 유로 변경",
                "new_pno": "←"
            },
            {
                "keywords": ["Motor Assembly,Sub", "Motor,AC", "Blower"],
                "change_point": "부품 변경",
                "change_reason": "기존 AC 모터에서 Blower(BLDC Motor 적용) 타입으로 변경",
                "new_pno": "TBD"
            },
            {
                "keywords": ["Water Tank", "Tank"],
                "change_point": "부품 변경",
                "change_reason": "기존 1L → 0.5L 변경 및 Assist Steam 기능을 위한 모듈 및 펌프 반영",
                "new_pno": "TBD"
            },
            {
                "keywords": ["Inverter", "MGT", "Magnetron"],
                "change_point": "부품 추가",
                "change_reason": "Oven + MWO 복합 기능을 위한 MGT 및 Inverter 신규 적용",
                "new_pno": "TBD"
            },

            # --- Panel ---
            {
                "keywords": ["Panel Assembly"],
                "change_point": "구조 변경",
                "change_reason": "제품 치수 축소에 따른 Rear Panel 크기 변경 및 Steam Heater VI type 적용",
                "new_pno": "TBD"
            },
            {
                "keywords": ["Panel,Rear", "Rear Panel"],
                "change_point": "사이즈 축소",
                "change_reason": "높이 축소(474.2→279.0mm) 및 BLDC Motor 적용에 따른 Supporter 반영",
                "new_pno": "←"
            },
            {
                "keywords": ["Generator,Steam", "Heater,Steam", "Steam Generator"],
                "change_point": "부품 변경",
                "change_reason": "VI type Steam Generator 적용",
                "new_pno": "TBD"
            },

            # --- Out Case ---
            {
                "keywords": ["Out Case Assembly", "OutCase"],
                "change_point": "사이즈 축소",
                "change_reason": "제품 치수 높이 137.0mm 감소에 따른 외관 부품 Size 변경",
                "new_pno": "TBD"
            },
            {
                "keywords": ["Panel,Side", "Side Panel"],
                "change_point": "사이즈 축소",
                "change_reason": "제품 높이 137mm 감소에 따른 측면 패널 높이 축소(567.8→430.8mm)",
                "new_pno": "←"
            },
            {
                "keywords": ["Cover,Top", "Cover,Base", "Cover Top", "Cover Base"],
                "change_point": "사이즈 축소",
                "change_reason": "제품 치수 축소에 따른 탑/베이스 커버 크기 변경 (Base 대비 공용화 적용)",
                "new_pno": "←"
            },

            # --- etc. ---
            {
                "keywords": ["Probe"],
                "change_point": "부품 변경",
                "change_reason": "1점인식 → 3점인식 Probe 적용에 따른 Spec 변경",
                "new_pno": "TBD"
            }
        ]

        # Master 행들을 순회하며 규칙 매칭
        for row in self.master_rows:
            pname = row['part_name'].upper()
            ptype = row['part_type'].upper()
            bpno = row['base_pno']

            # 1. Rule-based Match
            matched = False
            for rule in rules:
                for kw in rule['keywords']:
                    kw_up = kw.upper()
                    # Part Name 또는 Part Type에 키워드가 정확히 일치하거나 포함되는지 체크
                    if kw_up in pname or kw_up == ptype:
                        row['is_changed'] = True
                        row['refined_change_point'] = rule['change_point']
                        row['refined_change_reason'] = rule['change_reason']
                        row['refined_new_pno'] = rule['new_pno']
                        # Classification 업데이트 (신규 부품인 경우 New, 변경인 경우 Altered)
                        if rule['change_point'] in ["부품 추가", "신규"]:
                            row['refined_classification'] = "New"
                        elif rule['change_point'] in ["부품 변경", "구조 변경", "Spec 변경", "사이즈 축소"]:
                            row['refined_classification'] = "Altered"
                        matched = True
                        break
                if matched:
                    break

            # 2. PPT에 명시적으로 품번이 있는 경우 보정 (예: AEV73730303 -> TBD/변경사유 추가)
            if bpno == "AEV73730303":
                row['is_changed'] = True
                row['refined_change_point'] = "구조 변경"
                row['refined_change_reason'] = "Inverter, Fan, PCB, Water Tank 등 각종 부품 Assembly 조립 구조 반영 및 MWO 부품 안착 구조 반영"
                row['refined_new_pno'] = "TBD"
                row['refined_classification'] = "Altered"

            # 3. 기존에 이미 구체적인 사유가 적혀있고, 룰 매칭이 되지 않은 노드는 기존 상태를 보존하되 is_changed로 인정
            if not row['is_changed'] and row['change_point'] not in ['-', '하위 부품 변경', '']:
                row['is_changed'] = True
                row['refined_change_point'] = row['change_point']
                row['refined_change_reason'] = row['change_reason']
                row['refined_new_pno'] = row['new_pno']
                row['refined_classification'] = row['classification']

        # 4. LLM 쿼리 확장을 활용하여 매칭 보강
        # PPT 상세 리스트의 문장을 query로 사용해 expand_query 수행하고, 매칭 확률이 높은 노드 추가 매칭
        # 본 프로젝트의 로컬 테스트 환경을 위해 query_agent 기능을 연계 구동
        print("  - LLM 쿼리 확장 및 정밀 매칭 적용 중...")
        for change_item in detailed:
            detail_txt = change_item.get('change_detail', '')
            part_txt = change_item.get('part', '')
            category = change_item.get('category', '')
            
            if not detail_txt or detail_txt == '정보 없음':
                continue
                
            query = f"{category} {part_txt}: {detail_txt}"
            print(f"    * 쿼리 확장 실행: '{query[:40]}...'")
            try:
                expanded, verif = process_change_request(query)
                keywords = expanded.get('related_keywords', []) + expanded.get('synonyms', [])
                
                # 마스터 행 중 키워드가 매칭되는 후보 노드 추출
                candidates = []
                for idx, r in enumerate(self.master_rows):
                    if not r['is_changed']:
                        # 유사도 검색 후보군
                        rname = r['part_name'].lower()
                        if any(k.lower() in rname for k in keywords if len(k) > 1):
                            candidates.append({
                                'part_no': r['base_pno'],
                                'part_name': r['part_name'],
                                'index': idx
                            })
                
                if candidates:
                    # LLM 매칭
                    match_res = match_target_node_with_llm(candidates[:10], query, part_info=part_txt)
                    matched_pno = match_res.get('matched_part_no')
                    if matched_pno:
                        # 매칭된 노드 찾기
                        for r in self.master_rows:
                            if r['base_pno'] == matched_pno and not r['is_changed']:
                                r['is_changed'] = True
                                r['refined_change_point'] = "구조 변경" if "구조" in detail_txt or "조립" in detail_txt else "부품 변경"
                                r['refined_change_reason'] = detail_txt
                                r['refined_new_pno'] = "TBD"
                                r['refined_classification'] = "Altered"
                                print(f"      [LLM 매칭 완료] {matched_pno} ({r['part_name']}) -> {detail_txt[:30]}")
            except Exception as e:
                print(f"      [LLM 오류 무시] {e}")

    def propagate_changes(self) -> None:
        """하위 부품 변경 상태를 상위 어셈블리로 연쇄 전파합니다."""
        print("[4/5] 하위 부품 변경에 따른 상위 영향도 전파 중...")
        
        # 하위 노드에서 상위 노드로 역방향 전파를 수행하기 위해, depth가 깊은 노드부터 정렬
        sorted_rows_with_idx = sorted(enumerate(self.master_rows), key=lambda x: x[1]['lvl_depth'], reverse=True)

        for i, row in sorted_rows_with_idx:
            # 해당 노드가 변경 노드라면, 부모 노드를 추적하여 전파
            if row['is_changed'] or (row['refined_change_point'] and row['refined_change_point'] != '-'):
                parent_idx = row['parent_idx']
                if parent_idx is not None:
                    parent_row = self.master_rows[parent_idx]
                    # 부모 노드가 자체적인 상세 변경을 가지고 있지 않고 '-' 또는 '하위 부품 변경'인 경우 전파
                    if parent_row['refined_change_point'] in [None, '-', '하위 부품 변경']:
                        parent_row['refined_change_point'] = "하위 부품 변경"
                        parent_row['refined_change_reason'] = "하위부품 변경"
                        if parent_row['refined_new_pno'] in [None, '-']:
                            parent_row['refined_new_pno'] = "←"
                        if parent_row['refined_classification'] in [None, '-']:
                            parent_row['refined_classification'] = "Common"
                        # 부모도 변경 노드로 표시하여 상위로 계속 전파되도록 함
                        parent_row['is_changed'] = True

    def write_to_master_excel(self) -> None:
        """openpyxl을 사용하여 원본 서식을 완벽히 유지한 채 데이터만 덮어씁니다."""
        print("[5/5] 마스터 엑셀 파일 업데이트 및 저장 중...")
        wb = openpyxl.load_workbook(master_path, data_only=False)
        sheet = wb["Master"]

        update_count = 0
        for r in self.master_rows:
            row_num = r['row_num']

            # 변경점 (Col 10 -> J열)
            cp_val = r['refined_change_point'] if r['refined_change_point'] is not None else r['change_point']
            # 변경사유 (Col 11 -> K열)
            cr_val = r['refined_change_reason'] if r['refined_change_reason'] is not None else r['change_reason']
            # New P/No (Col 6 -> F열)
            np_val = r['refined_new_pno'] if r['refined_new_pno'] is not None else r['new_pno']
            # Classification (Col 13 -> M열)
            cls_val = r['refined_classification'] if r['refined_classification'] is not None else r['classification']

            # F열 (New P/No)
            if np_val and np_val != sheet.cell(row=row_num, column=6).value:
                sheet.cell(row=row_num, column=6, value=np_val)
                update_count += 1

            # J열 (변경점)
            if cp_val and cp_val != sheet.cell(row=row_num, column=10).value:
                sheet.cell(row=row_num, column=10, value=cp_val)
                update_count += 1

            # K열 (변경사유)
            if cr_val and cr_val != sheet.cell(row=row_num, column=11).value:
                sheet.cell(row=row_num, column=11, value=cr_val)
                update_count += 1

            # M열 (Classification)
            if cls_val and cls_val != sheet.cell(row=row_num, column=13).value:
                sheet.cell(row=row_num, column=13, value=cls_val)
                update_count += 1

        # Save workbook
        wb.save(master_path)
        wb.close()
        print(f"  - 마스터 엑셀 파일 업데이트 완료: {update_count}개 셀이 업데이트 되었습니다.")


def run_pipeline() -> None:
    processor = CompactOvenProcessor()
    processor.load_inputs()
    processor.build_master_hierarchy()
    processor.match_changes()
    processor.propagate_changes()
    processor.write_to_master_excel()
    print("\n[성공] Compact Oven 마스터 업데이트 파이프라인이 정상적으로 종료되었습니다.")


if __name__ == "__main__":
    run_pipeline()
