import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium w-fit whitespace-nowrap",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground border-transparent",
        secondary: "bg-secondary text-secondary-foreground border-transparent",
        destructive: "bg-destructive text-destructive-foreground border-transparent",
        success: "bg-success text-success-foreground border-transparent",
        warning: "bg-warning text-warning-foreground border-transparent",
        outline: "text-foreground border-border bg-card",
        // Fraud severity levels - same four severity-* tokens used by the FilterBar
        // chips, so a "critical" badge in a row table and a "critical" filter chip
        // read as the same color everywhere in the console.
        critical: "bg-severity-critical text-white border-transparent",
        high: "bg-severity-high text-white border-transparent",
        medium: "bg-severity-medium text-black border-transparent",
        low: "bg-severity-low text-white border-transparent",
      },
    },
    defaultVariants: { variant: "default" },
  }
)

function Badge({
  className,
  variant,
  ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
