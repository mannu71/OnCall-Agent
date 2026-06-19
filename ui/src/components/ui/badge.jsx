import * as React from "react"
import { cva } from "class-variance-authority"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
  {
    variants: {
      variant: {
        default:
          "border-transparent bg-primary text-primary-foreground shadow hover:bg-primary/80",
        secondary:
          "border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80",
        destructive:
          "border-transparent bg-destructive text-destructive-foreground shadow hover:bg-destructive/80",
        outline: "text-foreground",
        // Semantic status variants — use these instead of raw bg-*/text-* classes.
        success: "border-transparent bg-emerald-100 text-emerald-700 hover:bg-emerald-100/80 dark:bg-emerald-500/15 dark:text-emerald-300",
        warning: "border-transparent bg-amber-100 text-amber-800 hover:bg-amber-100/80 dark:bg-amber-500/15 dark:text-amber-300",
        danger: "border-transparent bg-red-100 text-red-700 hover:bg-red-100/80 dark:bg-red-500/15 dark:text-red-300",
        info: "border-transparent bg-blue-100 text-blue-700 hover:bg-blue-100/80 dark:bg-blue-500/15 dark:text-blue-300",
        muted: "border-transparent bg-slate-100 text-slate-600 hover:bg-slate-100/80 dark:bg-white/10 dark:text-slate-300",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Badge({
  className,
  variant,
  ...props
}) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  )
}

export { Badge, badgeVariants }
