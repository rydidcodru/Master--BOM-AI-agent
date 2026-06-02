"""BOM 계층 구조 파싱 및 트리 자료구조(In-Memory/Neo4j) 관리 모듈."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx
import pandas as pd


@dataclass
class BOMNode:
    """BOM 내의 개별 파트/어셈블리 노드 정보를 담는 데이터 클래스."""
    part_no: str
    part_name: str
    lvl: int
    parent_part_no: Optional[str] = None
    qty: float = 1.0
    supply_type: Optional[str] = None
    bom_path: str = ""
    extra_fields: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "part_no": self.part_no,
            "part_name": self.part_name,
            "lvl": self.lvl,
            "parent_part_no": self.parent_part_no,
            "qty": self.qty,
            "supply_type": self.supply_type,
            "bom_path": self.bom_path,
            **self.extra_fields
        }


class BOMRepository(ABC):
    """BOM 트리 저장을 위한 추상 리포지토리 인터페이스."""

    @abstractmethod
    def clear(self) -> None:
        """기존 트리 데이터를 비웁니다."""
        pass

    @abstractmethod
    def save_node(self, node: BOMNode) -> None:
        """BOM 노드를 저장소에 생성/업데이트합니다."""
        pass

    @abstractmethod
    def save_relationship(self, parent_no: str, child_no: str) -> None:
        """부모 노드와 자식 노드 간의 계층 관계(엣지)를 저장합니다."""
        pass

    @abstractmethod
    def get_node(self, part_no: str) -> Optional[BOMNode]:
        """지정한 품번의 노드 상세 정보를 가져옵니다."""
        pass

    @abstractmethod
    def get_sub_tree(self, part_no: str) -> List[BOMNode]:
        """지정한 서브 어셈블리 하위의 모든 자식 노드 목록을 탐색하여 반환합니다 (DFS/BFS 순회)."""
        pass

    @abstractmethod
    def get_parent_chain(self, part_no: str) -> List[str]:
        """특정 파트로부터 최상위 루트 노드까지의 부모 품번 경로 체인을 반환합니다."""
        pass

    @abstractmethod
    def find_nodes_by_keyword(self, keyword: str) -> List[BOMNode]:
        """부품명(part_name)에 키워드가 포함된 노드 목록을 검색합니다."""
        pass


class InMemoryBOMRepository(BOMRepository):
    """NetworkX DiGraph를 기반으로 작동하는 로컬 인메모리 BOM 리포지토리."""

    def __init__(self):
        self.graph = nx.DiGraph()

    def clear(self) -> None:
        self.graph.clear()

    def save_node(self, node: BOMNode) -> None:
        self.graph.add_node(node.part_no, **node.to_dict())

    def save_relationship(self, parent_no: str, child_no: str) -> None:
        if parent_no and child_no:
            # 부모에서 자식으로 향하는 유향 엣지 설정 (HAS_CHILD)
            self.graph.add_edge(parent_no, child_no)

    def get_node(self, part_no: str) -> Optional[BOMNode]:
        if not self.graph.has_node(part_no):
            return None
        attrs = self.graph.nodes[part_no]
        return self._attrs_to_node(part_no, attrs)

    def get_sub_tree(self, part_no: str) -> List[BOMNode]:
        if not self.graph.has_node(part_no):
            return []
        # DFS를 사용하여 하위 모든 자식 품번 목록 가져오기
        descendants = nx.descendants(self.graph, part_no)
        nodes = []
        for desc_no in descendants:
            attrs = self.graph.nodes[desc_no]
            nodes.append(self._attrs_to_node(desc_no, attrs))
        return nodes

    def get_parent_chain(self, part_no: str) -> List[str]:
        if not self.graph.has_node(part_no):
            return []
        
        chain = []
        curr = part_no
        # 유향 그래프에서 부모(predecessors)를 거슬러 올라감
        while True:
            preds = list(self.graph.predecessors(curr))
            if not preds:
                break
            # 트리 구조이므로 단일 부모를 가정
            curr = preds[0]
            chain.append(curr)
        return chain

    def find_nodes_by_keyword(self, keyword: str) -> List[BOMNode]:
        nodes = []
        kw_lower = keyword.lower()
        for node_id, attrs in self.graph.nodes(data=True):
            part_name = str(attrs.get("part_name", "")).lower()
            if kw_lower in part_name:
                nodes.append(self._attrs_to_node(node_id, attrs))
        return nodes

    def _attrs_to_node(self, part_no: str, attrs: dict) -> BOMNode:
        extra = {k: v for k, v in attrs.items() if k not in {
            "part_no", "part_name", "lvl", "parent_part_no", "qty", "supply_type", "bom_path"
        }}
        return BOMNode(
            part_no=part_no,
            part_name=attrs.get("part_name", ""),
            lvl=attrs.get("lvl", 1),
            parent_part_no=attrs.get("parent_part_no"),
            qty=attrs.get("qty", 1.0),
            supply_type=attrs.get("supply_type"),
            bom_path=attrs.get("bom_path", ""),
            extra_fields=extra
        )


class Neo4jBOMRepository(BOMRepository):
    """Neo4j 그래프 데이터베이스 기반 BOM 리포지토리."""

    def __init__(self, driver_factory):
        self.driver_factory = driver_factory

    def clear(self) -> None:
        driver = self.driver_factory()
        with driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
        driver.close()

    def save_node(self, node: BOMNode) -> None:
        driver = self.driver_factory()
        query = """
        MERGE (p:Part {part_no: $part_no})
        SET p.part_name = $part_name,
            p.lvl = $lvl,
            p.parent_part_no = $parent_part_no,
            p.qty = $qty,
            p.supply_type = $supply_type,
            p.bom_path = $bom_path
        """
        params = node.to_dict()
        with driver.session() as session:
            session.run(query, params)
        driver.close()

    def save_relationship(self, parent_no: str, child_no: str) -> None:
        driver = self.driver_factory()
        query = """
        MATCH (parent:Part {part_no: $parent_no})
        MATCH (child:Part {part_no: $child_no})
        MERGE (parent)-[:HAS_CHILD]->(child)
        """
        with driver.session() as session:
            session.run(query, parent_no=parent_no, child_no=child_no)
        driver.close()

    def get_node(self, part_no: str) -> Optional[BOMNode]:
        driver = self.driver_factory()
        query = """
        MATCH (p:Part {part_no: $part_no})
        RETURN p
        """
        node = None
        with driver.session() as session:
            res = session.run(query, part_no=part_no)
            rec = res.single()
            if rec:
                props = rec["p"]
                node = self._props_to_node(props)
        driver.close()
        return node

    def get_sub_tree(self, part_no: str) -> List[BOMNode]:
        driver = self.driver_factory()
        # Neo4j 가변 깊이 경로 탐색 (*1..) 사용
        query = """
        MATCH (:Part {part_no: $part_no})-[:HAS_CHILD*1..]->(child:Part)
        RETURN child
        """
        nodes = []
        with driver.session() as session:
            res = session.run(query, part_no=part_no)
            for record in res:
                props = record["child"]
                nodes.append(self._props_to_node(props))
        driver.close()
        return nodes

    def get_parent_chain(self, part_no: str) -> List[str]:
        driver = self.driver_factory()
        # 상위 부모 방향으로 거슬러 올라감
        query = """
        MATCH (child:Part {part_no: $part_no})<-[:HAS_CHILD*1..]-(parent:Part)
        RETURN parent.part_no AS parent_no, size((parent)<-[:HAS_CHILD*]-()) AS depth
        ORDER BY depth DESC
        """
        chain = []
        with driver.session() as session:
            res = session.run(query, part_no=part_no)
            for record in res:
                chain.append(record["parent_no"])
        driver.close()
        return chain

    def find_nodes_by_keyword(self, keyword: str) -> List[BOMNode]:
        driver = self.driver_factory()
        query = """
        MATCH (p:Part)
        WHERE toLower(p.part_name) CONTAINS toLower($keyword)
        RETURN p
        """
        nodes = []
        with driver.session() as session:
            res = session.run(query, keyword=keyword)
            for record in res:
                props = record["p"]
                nodes.append(self._props_to_node(props))
        driver.close()
        return nodes

    def _props_to_node(self, props) -> BOMNode:
        d = dict(props)
        part_no = d.pop("part_no")
        part_name = d.pop("part_name", "")
        lvl = d.pop("lvl", 1)
        parent_part_no = d.pop("parent_part_no", None)
        qty = d.pop("qty", 1.0)
        supply_type = d.pop("supply_type", None)
        bom_path = d.pop("bom_path", "")
        return BOMNode(
            part_no=part_no,
            part_name=part_name,
            lvl=lvl,
            parent_part_no=parent_part_no,
            qty=qty,
            supply_type=supply_type,
            bom_path=bom_path,
            extra_fields=d
        )


# ============================================================================
# 엑셀 BOM 파서 기능
# ============================================================================

def parse_bom_excel(file_path: str | Path) -> List[BOMNode]:
    """BOM 엑셀 파일을 읽어와 계층적 BOMNode 리스트를 파싱합니다."""
    file_path = Path(file_path)
    # ag-grid 또는 standard 엑셀 읽기
    df = pd.read_excel(file_path, sheet_name=0)

    # 1) 헤더 및 필수 컬럼명 매핑 자동 감지
    lvl_col = _detect_column(df, ["Lvl", "LVL", "Level", "LEVEL", "레벨"])
    pno_col = _detect_column(df, ["Part No", "P/NO", "P/NO.", "PNO", "품번", "부품번호", "PartNo"])
    name_col = _detect_column(df, ["Description", "DESC", "DESC.", "Desc", "부품명", "품명", "Part Name"])
    parent_col = _detect_column(df, ["Parent Part No(모)", "Parent Part No", "모품번", "ParentNo"])
    qty_col = _detect_column(df, ["Qty", "Quanty", "Quantity", "수량", "QTY"])
    supply_col = _detect_column(df, ["Supply Type", "SUPPLY TYPE", "공급형태"])

    if not lvl_col or not name_col or not pno_col:
        raise ValueError(
            f"엑셀 파일에서 필수 컬럼(Lvl, Part No, 부품명)을 탐지할 수 없습니다. "
            f"탐지된 컬럼: Lvl={lvl_col}, Part No={pno_col}, 부품명={name_col}"
        )

    nodes: List[BOMNode] = []
    path_stack: List[Tuple[int, str]] = []  # [(depth, part_no)]

    # DataFrame 행 순회 파싱
    for i, row in df.iterrows():
        lvl_raw = str(row.get(lvl_col, "") or "").strip()
        part_no = str(row.get(pno_col, "") or "").strip()
        part_name = str(row.get(name_col, "") or "").strip()
        parent_raw = str(row.get(parent_col, "") or "").strip() if parent_col else ""
        
        # 완전 비어 있는 정보 스킵
        if not part_no or part_no.lower() == "nan":
            continue

        # Lvl 표기 분석: '.1', '..2' 등 점 개수 계산. 점이 없으면 숫자 형태 파싱
        dot_count = len(lvl_raw) - len(lvl_raw.lstrip("."))
        if dot_count > 0:
            lvl_depth = dot_count
        else:
            try:
                # 숫자 파싱 시도
                lvl_depth = int(float(lvl_raw))
            except ValueError:
                lvl_depth = 1

        # Level 0은 모델 루트이므로, 트리 계층 스택 초기화
        if lvl_depth == 0:
            path_stack = [(0, part_no)]
            bom_path = part_no
            parent_part_no = None
        else:
            # 현재 레벨 바로 위 상위 부모 노드만 스택에 유지
            path_stack = [p for p in path_stack if p[0] < lvl_depth]
            
            parent_part_no = path_stack[-1][1] if path_stack else None
            # 수동으로 기재된 parent_raw가 존재하고 유효하다면 우선 매핑
            if parent_raw and parent_raw.lower() != "nan":
                parent_part_no = parent_raw

            path_stack.append((lvl_depth, part_no))
            bom_path = " > ".join([p[1] for p in path_stack])

        # 수량 및 타입 파싱
        try:
            qty_val = float(str(row.get(qty_col, 1) or 1).strip())
        except ValueError:
            qty_val = 1.0

        supply_type = str(row.get(supply_col, "")).strip() if supply_col else None
        if not supply_type or supply_type.lower() == "nan":
            supply_type = None

        node = BOMNode(
            part_no=part_no,
            part_name=part_name,
            lvl=lvl_depth,
            parent_part_no=parent_part_no,
            qty=qty_val,
            supply_type=supply_type,
            bom_path=bom_path
        )
        nodes.append(node)

    return nodes


def _detect_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    """유연한 정규화 비교로 후보 명칭 중 DataFrame 컬럼명을 찾아냅니다."""
    cols = {re.sub(r"[^A-Za-z0-9가-힣]", "", str(c).upper()): str(c) for c in df.columns}
    for cand in candidates:
        norm_cand = re.sub(r"[^A-Za-z0-9가-힣]", "", cand.upper())
        if norm_cand in cols:
            return cols[norm_cand]
    return None
