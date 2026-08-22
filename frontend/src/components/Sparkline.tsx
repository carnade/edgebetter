/** Recent-form bars. Height is earned runs per 9; taller is worse, so bad starts
 *  are marked in red rather than left for the reader to infer from height alone. */
interface Props {
  values: number[];
  max?: number;
  hotAbove?: number;
}

export function Sparkline({ values, max = 9, hotAbove = 5 }: Props) {
  if (!values.length) return <span className="dim">—</span>;
  return (
    <span className="spark" title={values.map((v) => v.toFixed(1)).join(" · ")}>
      {values.map((v, i) => (
        <i
          key={i}
          className={v >= hotAbove ? "hot" : ""}
          style={{ height: `${Math.max(2, Math.min(1, v / max) * 14)}px` }}
        />
      ))}
    </span>
  );
}
