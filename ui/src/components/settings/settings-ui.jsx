import React, { useEffect } from 'react';
import {
    Check,
    CheckCircle,
    Clock,
    X,
    XCircle,
} from 'lucide-react';
import { cn } from '@/lib/utils';

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

export const SBadge = ({ variant = 'default', dot, icon, children, className, title }) => (
    <span
        className={cn(
            'inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] font-semibold tracking-wide',
            BADGE_VARIANTS[variant] || BADGE_VARIANTS.default,
            className,
        )}
        title={title}
    >
        {dot && <span className="size-1.5 rounded-full bg-current" />}
        {icon}
        {children}
    </span>
);

const BUTTON_VARIANTS = {
    primary:
        'border-red-700 bg-gradient-to-b from-red-500 to-red-600 text-white shadow-[0_4px_10px_-3px_rgb(220_38_38/0.45)] hover:from-red-600 hover:to-red-700',
    outline:
        'border-border bg-background text-slate-700 shadow-sm hover:border-slate-300 hover:text-foreground',
    ghost: 'text-slate-500 hover:bg-slate-100 hover:text-foreground',
};

const BUTTON_SIZES = {
    md: 'px-3.5 py-2 text-[13px]',
    sm: 'px-2.5 py-1.5 text-xs',
    xs: 'px-2 py-1 text-xs',
    icon: 'size-8 p-0',
};

export const SButton = ({
    variant = 'outline',
    size = 'md',
    icon,
    children,
    className,
    ...rest
}) => (
    <button
        type="button"
        className={cn(
            'inline-flex items-center justify-center gap-1.5 rounded-lg border border-transparent font-medium whitespace-nowrap transition-all active:translate-y-px disabled:pointer-events-none disabled:opacity-50',
            BUTTON_VARIANTS[variant] || BUTTON_VARIANTS.outline,
            BUTTON_SIZES[size] || BUTTON_SIZES.md,
            '[&_svg]:size-3.5',
            size === 'icon' && '[&_svg]:size-4',
            className,
        )}
        {...rest}
    >
        {icon}
        {children}
    </button>
);

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

export const SInput = ({ mono, suffix, className, ...rest }) => {
    const inputClass = cn(INPUT, mono && 'font-mono text-xs', className);

    if (suffix) {
        return (
            <div className="flex overflow-hidden rounded-lg border border-border bg-background focus-within:border-slate-400 focus-within:ring-[3px] focus-within:ring-slate-400/20">
                <input className={cn(inputClass, 'border-0 shadow-none focus:ring-0')} {...rest} />
                {suffix}
            </div>
        );
    }
    return <input className={inputClass} {...rest} />;
};

export const SSelect = ({ options, className, ...rest }) => (
    <select className={cn(SELECT, className)} {...rest}>
        {options.map((o) => (
            <option key={o.value} value={o.value}>
                {o.label}
            </option>
        ))}
    </select>
);

export const STextarea = ({ className, ...props }) => (
    <textarea className={cn(INPUT, 'min-h-[84px] resize-y', className)} {...props} />
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

export const SDialog = ({ open, onClose, title, desc, children, footer, maxWidth }) => {
    useEffect(() => {
        if (!open) return undefined;
        const onKey = (e) => {
            if (e.key === 'Escape') onClose();
        };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [open, onClose]);

    if (!open) return null;

    return (
        <div
            className="fixed inset-0 z-[60] grid place-items-center bg-slate-950/55 p-4 backdrop-blur-[2px] animate-in fade-in duration-150 sm:p-5"
            onMouseDown={(e) => {
                if (e.target === e.currentTarget) onClose();
            }}
            role="presentation"
        >
            <div
                className={cn(
                    'relative flex max-h-[calc(100dvh-2rem)] w-full max-w-[540px] flex-col overflow-hidden rounded-[18px] border border-border bg-card shadow-2xl animate-in zoom-in-95 duration-150 sm:max-h-[calc(100vh-40px)]',
                )}
                style={maxWidth ? { maxWidth } : undefined}
                role="dialog"
                aria-modal="true"
            >
                {title && (
                    <div className="border-b border-border px-4 pb-4 pt-5 sm:px-6">
                        <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                                <h3 className="m-0 break-words text-base font-semibold text-foreground">{title}</h3>
                                {desc && <p className="m-0 mt-1 text-[13px] text-slate-500">{desc}</p>}
                            </div>
                            <SButton
                                variant="ghost"
                                size="icon"
                                onClick={onClose}
                                icon={<X className={ICON_SM} />}
                                aria-label="Close"
                            />
                        </div>
                    </div>
                )}
                {!title && (
                    <SButton
                        variant="ghost"
                        size="icon"
                        onClick={onClose}
                        aria-label="Close"
                        className="absolute right-3.5 top-3.5 z-10"
                        icon={<X className={ICON_SM} />}
                    />
                )}
                <div className="flex flex-col gap-4 overflow-y-auto px-4 py-5 sm:px-6">{children}</div>
                {footer && (
                    <div className="flex flex-col-reverse gap-2 border-t border-border bg-slate-50 px-4 py-4 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end sm:px-6 [&>button]:w-full [&>button]:sm:w-auto">
                        {footer}
                    </div>
                )}
            </div>
        </div>
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

export const DlgAlert = ({ variant = 'info', children, className }) => (
    <div
        className={cn(
            'rounded-lg px-3 py-2.5 text-xs leading-relaxed',
            variant === 'info' && 'border border-border bg-slate-50 text-slate-600',
            variant === 'success' && 'border border-emerald-200 bg-emerald-50 text-emerald-800',
            variant === 'warning' && 'border border-amber-300 bg-amber-50 text-amber-900',
            className,
        )}
    >
        {children}
    </div>
);

export const SToast = ({ message }) => (
    <div className="fixed bottom-7 left-1/2 z-[70] flex -translate-x-1/2 items-center gap-2.5 rounded-[10px] bg-slate-900 px-4 py-3 text-[13px] text-white shadow-lg animate-in slide-in-from-bottom-2 duration-200">
        <Check className="size-4 text-emerald-300" />
        <span>{message}</span>
    </div>
);
