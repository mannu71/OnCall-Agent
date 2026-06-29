import React from 'react';
import { Lock, Trash2, Upload } from 'lucide-react';
import { CARD, EMPTY_STATE, SButton } from './settings-ui';

export default function CertificatesSection({
    certificates,
    onUpload,
    onDelete,
}) {
    return (
        <>
            <div className={CARD}>
                {certificates.length === 0 ? (
                    <div className={EMPTY_STATE}>
                        <div className="mb-1 text-sm font-semibold text-slate-700">No certificates uploaded</div>
                        Upload a CA certificate for secure database connections.
                    </div>
                ) : (
                    certificates.map((filename) => (
                        <div
                            key={filename}
                            className="grid grid-cols-1 items-start gap-3 border-b border-slate-100 px-4 py-4 transition-colors last:border-b-0 hover:bg-slate-50 sm:grid-cols-[38px_1fr_auto] sm:items-center sm:gap-4 sm:px-6"
                        >
                            <div className="grid size-[38px] place-items-center rounded-[10px] bg-blue-50 text-blue-600">
                                <Lock className="size-4" />
                            </div>
                            <div>
                                <div className="mb-0.5 font-mono text-[13.5px] font-semibold text-foreground">
                                    {filename}
                                </div>
                                <div className="font-mono text-xs text-slate-500">/app/data/certs/{filename}</div>
                            </div>
                            <div className="flex items-center justify-end gap-3 sm:justify-start">
                                <SButton
                                    variant="ghost"
                                    size="icon"
                                    title="Delete"
                                    icon={<Trash2 className="size-4" />}
                                    onClick={() => onDelete(filename)}
                                />
                            </div>
                        </div>
                    ))
                )}
            </div>

            <div className="mt-3">
                <input
                    id="cert-upload"
                    type="file"
                    hidden
                    accept=".pem,.crt,.cer,.cert"
                    onChange={onUpload}
                />
                <label htmlFor="cert-upload" className="block cursor-pointer">
                    <div className="rounded-[10px] border border-dashed border-slate-300 bg-slate-50 px-5 py-7 text-center text-[13px] text-slate-500 transition-colors hover:border-red-400 hover:bg-red-50">
                        <div className="mb-1.5 flex justify-center">
                            <Upload className="size-[18px] text-slate-500" />
                        </div>
                        <strong className="font-semibold text-foreground">Drop a certificate here</strong>
                        {' '}
                        or click to browse
                        <div className="mt-1 text-xs text-slate-400">
                            Accepted: .pem, .crt, .cer, .cert — max 64 kB
                        </div>
                    </div>
                </label>
            </div>
        </>
    );
}
