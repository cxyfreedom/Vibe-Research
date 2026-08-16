import { type ReactNode } from "react";

interface Props {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}

export function PageHeader({ title, subtitle, actions }: Props) {
  return (
    <div className="mb-5 flex min-w-0 flex-wrap items-start justify-between gap-3 md:mb-6 md:items-end">
      <div className="min-w-0">
        <h1 className="text-xl font-extrabold tracking-tight text-glow sm:text-2xl">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>}
      </div>
      {actions && <div className="flex w-full min-w-0 flex-wrap items-center gap-2 sm:w-auto">{actions}</div>}
    </div>
  );
}
