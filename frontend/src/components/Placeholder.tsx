// Заглушка раздела, который будет реализован на следующих шагах плана.

export default function Placeholder({
  title,
  step,
}: {
  title: string;
  step: string;
}) {
  return (
    <div className="placeholder">
      <div className="panel placeholder-card">
        <h2>{title}</h2>
        <p className="note">Раздел в разработке — {step} плана внедрения UI.</p>
      </div>
    </div>
  );
}
