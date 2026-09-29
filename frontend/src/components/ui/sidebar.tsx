import * as React from "react"

import { cn } from "@/lib/utils"

// Hand-written, not shadcn's compound Sidebar primitive - a desktop-first internal
// console has no need for the mobile drawer/collapse-state machinery that primitive
// carries. Same naming convention as Mandate360's own sidebar shell (dark-navy
// persistent left rail, flat nav + section labels, avatar+role footer), deliberately
// matched across Finverge's console products rather than invented fresh here.

export function SidebarShell({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("flex h-svh overflow-hidden", className)} {...props} />
}

export function Sidebar({ className, ...props }: React.ComponentProps<"aside">) {
  return (
    <aside
      className={cn(
        "flex w-64 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground",
        className
      )}
      {...props}
    />
  )
}

export function SidebarHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("flex items-center gap-2.5 border-b border-sidebar-border px-4 py-4", className)}
      {...props}
    />
  )
}

export function SidebarNav({ className, ...props }: React.ComponentProps<"nav">) {
  return <nav className={cn("flex-1 space-y-4 overflow-y-auto px-3 py-4", className)} {...props} />
}

export function SidebarSectionLabel({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "px-2 pt-2 pb-1 text-[11px] font-semibold tracking-widest text-sidebar-foreground/50 uppercase",
        className
      )}
      {...props}
    />
  )
}

export function SidebarNavItem({
  icon: Icon,
  active,
  className,
  children,
  ...props
}: React.ComponentProps<"button"> & { icon?: React.ComponentType<{ className?: string }>; active?: boolean }) {
  return (
    <button
      type="button"
      data-active={active}
      className={cn(
        "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm font-medium transition-colors",
        "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
        active
          ? "bg-sidebar-accent text-sidebar-accent-foreground"
          : "text-sidebar-foreground/85",
        className
      )}
      {...props}
    >
      {Icon && <Icon className="size-4 shrink-0" />}
      <span className="truncate">{children}</span>
    </button>
  )
}

export function SidebarFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div className={cn("space-y-1 border-t border-sidebar-border px-3 py-3", className)} {...props} />
  )
}

export function SidebarMain({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("flex flex-1 flex-col overflow-hidden bg-background", className)} {...props} />
}

export function ContentHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("flex items-center justify-between gap-3 border-b border-border bg-card px-5 py-2.5", className)}
      {...props}
    />
  )
}

export function ContentBody({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("flex-1 overflow-y-auto p-5", className)} {...props} />
}

export function PageHeader({
  title,
  description,
  action,
  className,
}: {
  title: string
  description?: string
  action?: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn("mb-4 flex flex-wrap items-start justify-between gap-3", className)}>
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  )
}

// A tight label-over-value cell for dense key-value grids (Verafye-style detail
// panels: many small stats packed per row, not one field per line).
export function StatCell({
  label,
  value,
  tone,
  className,
}: {
  label: string
  value: React.ReactNode
  tone?: "default" | "success" | "warning" | "destructive"
  className?: string
}) {
  const toneClass = {
    default: "text-foreground",
    success: "text-success",
    warning: "text-warning",
    destructive: "text-destructive",
  }[tone ?? "default"]
  return (
    <div className={cn("min-w-0", className)}>
      <div className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">{label}</div>
      <div className={cn("text-sm leading-snug font-semibold", toneClass)}>{value}</div>
    </div>
  )
}

// Initials from a name or role string ("tenant_admin" -> "TA"; "board" -> "BO") - there
// is no display-name field on the session to draw a real name from, so the role itself
// is the best identity signal available, same shape shadcn's own avatar fallback takes.
export function UserAvatar({ name, className }: { name: string; className?: string }) {
  const initials = React.useMemo(() => {
    const parts = name.replace(/_/g, " ").trim().split(/\s+/)
    const letters = parts.length >= 2 ? parts[0][0] + parts[1][0] : name.slice(0, 2)
    return letters.toUpperCase()
  }, [name])
  return (
    <div
      className={cn(
        "flex size-8 shrink-0 items-center justify-center rounded-full bg-sidebar-accent text-xs font-semibold text-sidebar-accent-foreground",
        className
      )}
    >
      {initials}
    </div>
  )
}
