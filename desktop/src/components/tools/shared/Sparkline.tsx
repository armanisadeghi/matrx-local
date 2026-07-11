import { useMemo } from "react";
import { cn } from "@/lib/utils";

interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  color?: string;
  gradient?: boolean;
  showDot?: boolean;
  className?: string;
  min?: number;
  max?: number;
}

export function Sparkline({
  data,
  width = 120,
  height = 32,
  color = "violet",
  gradient = true,
  showDot = true,
  className,
  min: fixedMin,
  max: fixedMax,
}: SparklineProps) {
  const points = useMemo(() => {
    if (data.length < 2) return "";
    const min = fixedMin ?? Math.min(...data);
    const max = fixedMax ?? Math.max(...data);
    const range = max - min || 1;
    const pad = 2;
    const w = width - pad * 2;
    const h = height - pad * 2;
    const step = w / (data.length - 1);

    return data
      .map((v, i) => {
        const x = pad + i * step;
        const y = pad + h - ((v - min) / range) * h;
        return `${x},${y}`;
      })
      .join(" ");
  }, [data, width, height, fixedMin, fixedMax]);

  const fillPoints = useMemo(() => {
    if (!gradient || data.length < 2) return "";
    const min = fixedMin ?? Math.min(...data);
    const max = fixedMax ?? Math.max(...data);
    const range = max - min || 1;
    const pad = 2;
    const w = width - pad * 2;
    const h = height - pad * 2;
    const step = w / (data.length - 1);

    const linePoints = data.map((v, i) => {
      const x = pad + i * step;
      const y = pad + h - ((v - min) / range) * h;
      return `${x},${y}`;
    });

    const lastX = pad + (data.length - 1) * step;
    return `${pad},${height} ${linePoints.join(" ")} ${lastX},${height}`;
  }, [data, width, height, gradient, fixedMin, fixedMax]);

  const lastPoint = useMemo(() => {
    if (data.length < 2) return null;
    const min = fixedMin ?? Math.min(...data);
    const max = fixedMax ?? Math.max(...data);
    const range = max - min || 1;
    const pad = 2;
    const w = width - pad * 2;
    const h = height - pad * 2;
    const step = w / (data.length - 1);
    const i = data.length - 1;
    const last = data[i];
    if (last === undefined) return null;
    return {
      x: pad + i * step,
      y: pad + h - ((last - min) / range) * h,
    };
  }, [data, width, height, fixedMin, fixedMax]);

  const gradientId = useMemo(() => `spark-grad-${Math.random().toString(36).slice(2, 8)}`, []);

  const strokeClass = `stroke-${color}-500`;
  const fillClass = `fill-${color}-500`;

  if (data.length < 2) {
    return (
      <div
        className={cn("flex items-center justify-center text-[10px] text-muted-foreground", className)}
        style={{ width, height }}
      >
        No data
      </div>
    );
  }

  return (
    <svg width={width} height={height} className={cn("overflow-visible", className)}>
      {gradient && (
        <defs>
          <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" className={fillClass} stopOpacity="0.3" />
            <stop offset="100%" className={fillClass} stopOpacity="0" />
          </linearGradient>
        </defs>
      )}
      {gradient && fillPoints && (
        <polygon points={fillPoints} fill={`url(#${gradientId})`} />
      )}
      <polyline
        points={points}
        fill="none"
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
        className={strokeClass}
      />
      {showDot && lastPoint && (
        <circle
          cx={lastPoint.x}
          cy={lastPoint.y}
          r="2.5"
          className={cn(fillClass, strokeClass)}
          strokeWidth="1"
        />
      )}
    </svg>
  );
}
