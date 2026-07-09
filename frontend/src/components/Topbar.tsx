import type { ReactNode } from "react";

interface Props {
  title: string;
  crumbs?: { label: string; href?: string }[];
  right?: ReactNode;
}

export function Topbar({ title, crumbs, right }: Props) {
  return (
    <div className="tb">
      <div>
        <div className="tb__title">{title}</div>
        {crumbs && crumbs.length > 0 && (
          <div className="tb__crumb">
            {crumbs.map((c, i) => (
              <span key={i}>
                {i > 0 && <span>/</span>}
                {c.label}
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="tb__right">{right}</div>
    </div>
  );
}
