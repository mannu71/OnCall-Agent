import { useCallback, useEffect, useRef, useState } from 'react';

export function usePersistedState(key, defaultValue) {
    const [value, setValue] = useState(() => {
        const stored = localStorage.getItem(key);
        if (stored === null) return defaultValue;
        if (typeof defaultValue === 'boolean') return stored !== 'false';
        return stored;
    });

    useEffect(() => {
        localStorage.setItem(key, String(value));
    }, [key, value]);

    return [value, setValue];
}

export function useSettingsToast(durationMs = 2200) {
    const [toast, setToast] = useState('');
    const timerRef = useRef(null);

    const showToast = useCallback((message) => {
        if (timerRef.current) clearTimeout(timerRef.current);
        setToast(message);
        timerRef.current = setTimeout(() => setToast(''), durationMs);
    }, [durationMs]);

    useEffect(() => () => {
        if (timerRef.current) clearTimeout(timerRef.current);
    }, []);

    return { toast, showToast };
}
