import React, { useEffect } from 'react';
import {
    Check,
    CheckCircle,
    Clock,
    X,
    XCircle,
} from 'lucide-react';
import { cn } from '@/lib/utils';

// Import official Shadcn UI components
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogDescription,
    DialogFooter,
} from '@/components/ui/dialog';

export { cn };
export const sClasses = cn;

export const ICON_SM = 'size-4 shrink-0';
export const ICON_XS = 'size-3 shrink-0';

export const CARD =
    'w-full min-w-0 overflow-hidden rounded-[14px] border border-border bg-card shadow-sm';

export const CARD_CONTAINER = CARD;

export const TABLE_WRAP = 'custom-scrollbar w-full overflow-x-auto';

/** Container width (px) at which MCP tables replace stacked cards. */
export const TABLE_BREAKPOINT_MD = 560;

export const LIST_CARD =
    'min-w-0 border-b border-slate-100 px-4 py-4 last:border-b-0 sm:px-6';

export const TABLE =
    'w-full min-w-[40rem] border-collapse text-[13.5px]';

export const TABLE_WIDE =
    'w-full min-w-[52rem] border-collapse text-[13.5px]';

export const TABLE_HEAD =
    'whitespace-nowrap bg-slate-50 px-4 py-3 text-left text-[10.5px] font-bold uppercase tracking-wider text-slate-500 border-b border-border sm:px-6';

export const TABLE_CELL =
    'whitespace-nowrap px-4 py-4 border-b border-slate-100 text-slate-700 align-middle sm:px-6';

export const EMPTY_STATE = 'px-4 py-14 text-center text-[13px] text-slate-500 sm:px-6';

export const INPUT =
    'w-full rounded-lg border border-border bg-background px-3 py-2 text-[13.5px] text-foreground outline-none transition-[border-color,box-shadow] focus:border-slate-400 focus:ring-[3px] focus:ring-slate-400/20';

export const SELECT = cn(
    INPUT,
    'appearance-none bg-[length:14px] bg-[right_11px_center] bg-no-repeat pr-9',
    "bg-[url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")]",
);

const BADGE_VARIANTS = {
    default: 'bg-slate-100 text-slate-700',
    success: 'bg-emerald-100 text-emerald-700',
    warning: 'bg-amber-100 text-amber-700',
    error: 'bg-red-100 text-red-700',
    info: 'bg-blue-100 text-blue-700',
    violet: 'bg-violet-100 text-violet-700',
    muted: 'bg-slate-100 text-slate-500',
    outline: 'border border-border bg-transparent text-slate-500',
};

// Use Shadcn Badge under the hood
export const SBadge = ({ variant = 'default', dot, icon, children, className, title }) => (
    <Badge
        variant="outline"
        className={cn(
            'inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] font-semibold tracking-wide border-0 shadow-none',
            BADGE_VARIANTS[variant] || BADGE_VARIANTS.default,
            className,
        )}
        title={title}
    >
        {dot && <span className="size-1.5 rounded-full bg-current" />}
        {icon}
        {children}
    </Badge>
);

// Use Shadcn Button under the hood
export const SButton = ({
    variant = 'outline',
    size = 'md',
    icon,
    children,
    className,
    ...rest
}) => {
    let shadcnVariant = variant;
    if (variant === 'primary') {
        shadcnVariant = 'default';
    }

    let shadcnSize = size;
    if (size === 'md') {
        shadcnSize = 'default';
    }

    return (
        <Button
            variant={shadcnVariant}
            size={shadcnSize}
            className={cn(
                'font-medium whitespace-nowrap transition-all active:translate-y-px disabled:pointer-events-none disabled:opacity-50',
                className
            )}
            {...rest}
        >
            {icon && <span className="inline-flex shrink-0 items-center justify-center [&_svg]:size-3.5">{icon}</span>}
            {children}
        </Button>
    );
};

export const SToggle = ({ checked, onChange, className, ...rest }) => (
    <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange && onChange(!checked)}
        className={cn(
            'relative inline-flex h-[22px] w-10 shrink-0 rounded-full border-0 p-0 transition-colors',
            checked ? 'bg-gradient-to-b from-red-500 to-red-600' : 'bg-slate-200',
            className,
        )}
        {...rest}
    >
        <span
            className={cn(
                'absolute top-0.5 left-0.5 size-[18px] rounded-full bg-white shadow transition-transform',
                checked && 'translate-x-[18px]',
            )}
        />
    </button>
);

export const SSegmented = ({ value, onChange, options }) => (
    <div className="inline-flex gap-0.5 rounded-[9px] bg-slate-100 p-0.5" role="radiogroup">
        {options.map((o) => (
            <button
                key={o.value}
                type="button"
                className={cn(
                    'inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium text-slate-600 transition-all',
                    value === o.value
                        ? 'bg-background text-foreground shadow-sm'
                        : 'hover:text-foreground',
                )}
                onClick={() => onChange(o.value)}
                role="radio"
                aria-checked={value === o.value}
            >
                {o.icon}
                {o.label}
            </button>
        ))}
    </div>
);

export const SField = ({ label, help, children }) => (
    <div className="grid grid-cols-1 items-start gap-3 border-b border-slate-100 px-4 py-4 last:border-b-0 sm:px-6 sm:py-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] lg:items-center lg:gap-8 xl:gap-9">
        <div className="min-w-0">
            <div className="mb-1 text-[13.5px] font-semibold text-foreground">{label}</div>
            {help && <div className="text-xs leading-relaxed text-slate-500">{help}</div>}
        </div>
        <div className="min-w-0 w-full lg:justify-self-stretch">{children}</div>
    </div>
);

// Use Shadcn Input under the hood
export const SInput = ({ mono, suffix, className, ...rest }) => {
    const inputClass = cn(
        "h-8 text-[13.5px]",
        mono && 'font-mono text-xs',
        className
    );

    if (suffix) {
        return (
            <div className="flex w-full items-stretch overflow-hidden rounded-lg border border-input bg-background focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50">
                <input
                    className={cn(
                        "h-8 w-full min-w-0 bg-transparent px-2.5 py-1 text-base md:text-sm outline-none border-0 shadow-none focus:ring-0 focus-visible:ring-0 focus-visible:border-0",
                        mono && 'font-mono text-xs',
                        className
                    )}
                    {...rest}
                />
                <div className="flex shrink-0 items-center justify-center border-l border-input">
                    {suffix}
                </div>
            </div>
        );
    }
    return <Input className={inputClass} {...rest} />;
};

// Use Shadcn Select under the hood
export const SSelect = ({ options, value, onChange, className, disabled, ...rest }) => {
    return (
        <Select
            value={value}
            onValueChange={(val) => onChange?.({ target: { value: val } })}
            disabled={disabled}
            {...rest}
        >
            <SelectTrigger className={cn("h-8 text-[13.5px] bg-background border-input", className)}>
                <SelectValue />
            </SelectTrigger>
            <SelectContent>
                {options.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                        {o.label}
                    </SelectItem>
                ))}
            </SelectContent>
        </Select>
    );
};

// Use Shadcn Textarea under the hood
export const STextarea = ({ className, mono, ...props }) => (
    <Textarea
        className={cn(
            'min-h-[84px] resize-y text-[13.5px]',
            mono && 'font-mono text-xs',
            className
        )}
        {...props}
    />
);

export const SSection = ({ id, title, desc, actions, icon, children }) => (
    <div className="mb-8 w-full" id={id}>
        <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
            <div className="flex min-w-0 items-start gap-3 sm:items-center">
                {icon && (
                    <span className="grid size-[30px] shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-500 [&_svg]:size-4">
                        {icon}
                    </span>
                )}
                <div className="min-w-0">
                    <h2 className="m-0 text-base font-semibold tracking-tight text-foreground">{title}</h2>
                    {desc && <p className="m-0 text-xs leading-relaxed text-slate-500">{desc}</p>}
                </div>
            </div>
            {actions && (
                <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:flex-wrap sm:justify-end [&>button]:w-full [&>button]:sm:w-auto">
                    {actions}
                </div>
            )}
        </div>
        {children}
    </div>
);

export const SStatusBadge = ({ status, message }) => {
    if (status === 'testing') {
        return (
            <SBadge variant="muted" icon={<Clock className={ICON_XS} />}>
                Testing
            </SBadge>
        );
    }
    if (status === 'connected') {
        return (
            <SBadge variant="success" icon={<CheckCircle className={ICON_XS} />}>
                Connected
            </SBadge>
        );
    }
    if (status === 'error') {
        return (
            <SBadge variant="error" icon={<XCircle className={ICON_XS} />} title={message}>
                {message ? message.substring(0, 18) : 'Error'}
            </SBadge>
        );
    }
    return (
        <SBadge variant="outline" dot>
            Not tested
        </SBadge>
    );
};

// Use Shadcn Dialog components under the hood
export const SDialog = ({ open, onClose, title, desc, children, footer, maxWidth }) => {
    return (
        <Dialog open={open} onOpenChange={(openState) => {
            if (!openState) onClose?.();
        }}>
            <DialogContent
                className="max-h-[calc(100dvh-2rem)] overflow-hidden flex flex-col p-0 border border-border bg-card shadow-2xl sm:max-h-[calc(100vh-40px)] gap-0"
                style={maxWidth ? { maxWidth, width: '100%' } : undefined}
            >
                {title && (
                    <DialogHeader className="border-b border-border px-4 pb-4 pt-5 sm:px-6 flex flex-col space-y-1.5 text-left">
                        <DialogTitle className="text-base font-semibold text-foreground leading-none tracking-tight">
                            {title}
                        </DialogTitle>
                        {desc && (
                            <DialogDescription className="text-[13px] text-slate-500 mt-1">
                                {desc}
                            </DialogDescription>
                        )}
                    </DialogHeader>
                )}
                <div className="flex-1 overflow-y-auto px-4 py-5 sm:px-6 flex flex-col gap-4">
                    {children}
                </div>
                {footer && (
                    <DialogFooter className="flex flex-col-reverse gap-2 border-t border-border bg-slate-50 px-4 py-4 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end sm:px-6 [&>button]:w-full [&>button]:sm:w-auto">
                        {footer}
                    </DialogFooter>
                )}
            </DialogContent>
        </Dialog>
    );
};

export const SDlgField = ({ label, hint, children }) => (
    <div>
        <label className="mb-1.5 flex items-center justify-between text-xs font-semibold text-slate-700">
            <span>{label}</span>
            {hint && <span className="font-normal text-slate-400">{hint}</span>}
        </label>
        {children}
    </div>
);

// Use Shadcn Alert components under the hood
export const DlgAlert = ({ variant = 'info', children, className }) => {
    return (
        <Alert
            className={cn(
                'rounded-lg px-3 py-2.5 text-xs leading-relaxed border',
                variant === 'info' && 'border-border bg-slate-50 text-slate-600',
                variant === 'success' && 'border-emerald-200 bg-emerald-50 text-emerald-800',
                variant === 'warning' && 'border-amber-300 bg-amber-50 text-amber-900',
                className
            )}
        >
            <AlertDescription className="text-xs text-inherit">
                {children}
            </AlertDescription>
        </Alert>
    );
};

export const SToast = ({ message }) => (
    <div className="fixed bottom-7 left-1/2 z-[70] flex -translate-x-1/2 items-center gap-2.5 rounded-[10px] bg-slate-900 px-4 py-3 text-[13px] text-white shadow-lg animate-in slide-in-from-bottom-2 duration-200">
        <Check className="size-4 text-emerald-300" />
        <span>{message}</span>
    </div>
);

