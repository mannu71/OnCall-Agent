import { useMemo } from 'react';
import { buildGalaxy } from './galaxy/galaxyLayout';

// Build the spiral layout ONCE per fetched dataset.
//
// The dependency here is deliberately the raw `data`, not the filtered view.
// Folder sizes drive arm segment widths, so memoizing on filtered nodes would
// make every filter chip toggle re-shape the galaxy and teleport every star.
// Filtering happens downstream, on the already-positioned nodes.
export function useGalaxy(data) {
  return useMemo(() => {
    if (!data?.nodes) return null;
    return buildGalaxy(data.nodes, data.edges ?? []);
  }, [data]);
}
