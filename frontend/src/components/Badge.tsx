import { KIND_META, type ChangeKind } from "../util";

export function Badge({ kind, small }: { kind: ChangeKind; small?: boolean }) {
  const meta = KIND_META[kind];
  return (
    <span className={`badge ${meta.cls} ${small ? "badge-sm" : ""}`}>
      <span className="badge-icon">{meta.icon}</span>
      {meta.label}
    </span>
  );
}
