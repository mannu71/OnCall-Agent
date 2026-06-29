import * as React from "react"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"

/**
 * Field — a labelled form-control wrapper (Label + control + optional hint/error).
 *
 * Standardises the repeated `<Label/> + <Input/>` + helper-text pattern in forms.
 * Pass the control as children; `htmlFor`/`id` wire the label for accessibility.
 */
function Field({ label, htmlFor, hint, error, required, className, children, ...props }) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)} {...props}>
      {label ? (
        <Label htmlFor={htmlFor} className="text-xs font-medium">
          {label}
          {required ? <span className="ml-0.5 text-destructive">*</span> : null}
        </Label>
      ) : null}
      {children}
      {error ? (
        <p className="text-xs text-destructive">{error}</p>
      ) : hint ? (
        <p className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  )
}

export { Field }
