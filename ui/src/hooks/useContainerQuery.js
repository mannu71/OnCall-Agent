import { useEffect, useState } from 'react';

function readContainerWidth(entry) {
    if (entry.contentBoxSize?.length) {
        return entry.contentBoxSize[0].inlineSize;
    }
    return entry.contentRect.width;
}

/** True when the observed element is at least `minWidth` px wide. */
export function useContainerQuery(ref, minWidth) {
    const [matches, setMatches] = useState(false);

    useEffect(() => {
        const node = ref.current;
        if (!node || !minWidth) return undefined;

        const update = (width) => {
            setMatches(width >= minWidth);
        };

        const observer = new ResizeObserver((entries) => {
            const entry = entries[0];
            if (entry) update(readContainerWidth(entry));
        });

        observer.observe(node);
        update(node.getBoundingClientRect().width);

        return () => observer.disconnect();
    }, [ref, minWidth]);

    return matches;
}
