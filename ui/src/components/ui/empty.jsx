import * as React from "react"
import { cn } from "@/lib/utils"

/**
 * Empty — a consistent empty-state placeholder for lists/tables.
 *
 * Replaces the various ad-hoc "No X found" blocks. Optional `icon` (a lucide
 * component), `title`, and `description`; children render below (e.g. an action).
 */
function Empty({ icon: Icon, title, description, className, children, ...props }) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-1.5 px-4 py-10 text-center",
        className
      )}
      {...props}
    >
      {Icon ? <Icon className="mb-1 h-6 w-6 text-muted-foreground/60" /> : null}
      {title ? <p className="text-sm font-medium text-foreground">{title}</p> : null}
      {description ? (
        <p className="max-w-sm text-xs text-muted-foreground">{description}</p>
      ) : null}
      {children}
    </div>
  )
}

export { Empty }
