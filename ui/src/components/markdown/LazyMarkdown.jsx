import React, { lazy, Suspense } from 'react';

const MarkdownMessage = lazy(() => import('./MarkdownMessage.jsx'));

export function LazyMarkdown({ content, className }) {
  if (!content) return null;
  return (
    <Suspense fallback={<div className="text-muted-foreground text-sm animate-pulse">Loading…</div>}>
      <div className={className}>
        <MarkdownMessage content={content} />
      </div>
    </Suspense>
  );
}
