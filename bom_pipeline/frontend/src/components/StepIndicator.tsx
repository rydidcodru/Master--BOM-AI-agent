interface Props {
  current: number;
}

const steps = ['1. 파일 업로드', '2. BOM 매핑 확인', '3. 이력 후보 검토', '4. BOM 편집'];

export default function StepIndicator({ current }: Props) {
  return (
    <div style={{ display: 'flex', gap: 0, marginBottom: 24 }}>
      {steps.map((label, i) => {
        const idx = i + 1;
        const done    = idx < current;
        const active  = idx === current;
        return (
          <div
            key={idx}
            style={{
              flex: 1,
              padding: '10px 0',
              textAlign: 'center',
              fontWeight: active ? 700 : 400,
              fontSize: 14,
              borderBottom: active
                ? '3px solid #2563eb'
                : done
                ? '3px solid #86efac'
                : '3px solid #e5e7eb',
              color: active ? '#2563eb' : done ? '#16a34a' : '#9ca3af',
              background: '#fff',
            }}
          >
            {done ? '✅ ' : active ? '▶ ' : '○ '}{label}
          </div>
        );
      })}
    </div>
  );
}