// Знак ChemSource AI: шестиугольник бензольного кольца, собранный из шести
// узлов. Узел — источник: поставщик, площадка, реестр. Один из них акцентный —
// тот, ради которого поиск и затевается: подтверждённый изготовитель.
//
// Знак нарисован кодом, а не картинкой: на заставке он собирается по шагам,
// а SVG в разметке не требует отдельного запроса к сети при первой загрузке.

const R = 22;
const CENTER = 32;

// Вершины «остриём вверх»: -90°, затем через 60°.
const VERTICES = [0, 1, 2, 3, 4, 5].map((index) => {
  const angle = ((-90 + index * 60) * Math.PI) / 180;
  return {
    x: +(CENTER + R * Math.cos(angle)).toFixed(2),
    y: +(CENTER + R * Math.sin(angle)).toFixed(2),
  };
});

const HEX_PATH = `${VERTICES.map((p, i) => `${i === 0 ? "M" : "L"}${p.x} ${p.y}`).join(" ")} Z`;

export function LogoMark({
  size = 28,
  animated = false,
}: {
  size?: number;
  animated?: boolean;
}) {
  return (
    <svg
      className={`logo-mark${animated ? " is-animated" : ""}`}
      viewBox="0 0 64 64"
      width={size}
      height={size}
      role="img"
      aria-label="ChemSource AI"
    >
      {/* Связи между узлами: рисуются обводкой по кругу. */}
      <path className="logo-bonds" d={HEX_PATH} />
      {/* Ароматическое кольцо внутри — второй шаг сборки. */}
      <circle className="logo-ring" cx={CENTER} cy={CENTER} r={11} />
      {VERTICES.map((point, index) => (
        <circle
          key={index}
          className={`logo-node${index === 0 ? " is-accent" : ""}`}
          style={{ ["--i" as string]: index }}
          cx={point.x}
          cy={point.y}
          r={index === 0 ? 5 : 4}
        />
      ))}
    </svg>
  );
}

export function LogoWord({ className }: { className?: string }) {
  return (
    <span className={`logo-word${className ? ` ${className}` : ""}`}>
      Chem<b>Source</b>
      <i>AI</i>
    </span>
  );
}

// Заставка первой загрузки. Показывается, пока проверяется сессия, и держится
// заданный минимум — иначе на быстром соединении сборка знака мелькнёт.
export default function SplashScreen() {
  return (
    <div className="splash">
      <div className="splash-inner">
        <LogoMark size={104} animated />
        <LogoWord className="splash-word" />
      </div>
    </div>
  );
}
