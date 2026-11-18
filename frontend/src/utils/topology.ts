import type { Topology } from '@/api/types'

export interface TopologyGraphOption {
  tooltip: { trigger: 'item'; formatter: (params: unknown) => string }
  legend: { data: string[]; textStyle: { color: string } }
  series: Array<Record<string, unknown>>
}

const KIND_COLORS: Record<string, string> = {
  gateway: '#f59e0b',
  lb: '#f59e0b',
  service: '#38bdf8',
  app: '#38bdf8',
  db: '#a78bfa',
  database: '#a78bfa',
  cache: '#34d399',
  mq: '#f472b6',
  queue: '#f472b6',
  host: '#94a3b8',
  external: '#64748b',
}

const FALLBACK_COLORS = ['#38bdf8', '#a78bfa', '#34d399', '#f59e0b', '#f472b6', '#22d3ee', '#94a3b8']

function colorForKind(kind: string, index: number): string {
  return KIND_COLORS[kind] ?? FALLBACK_COLORS[index % FALLBACK_COLORS.length] ?? '#38bdf8'
}

/**
 * 为拓扑计算稳定的分层布局（按上游到下游的层级），避免 force 布局在轮询时抖动。
 * 边方向 upstream -> downstream，层级从左到右。
 */
export function computeLevels(topology: Topology): Map<string, number> {
  const ids = topology.nodes.map((node) => node.id)
  const level = new Map<string, number>()
  for (const id of ids) level.set(id, 0)
  const valid = new Set(ids)
  const edges = topology.edges.filter(
    (edge) => valid.has(edge.upstream) && valid.has(edge.downstream),
  )

  // 松弛求最长路径层级；迭代上限即节点数，可容忍环（环内不会无限增长）
  for (let round = 0; round < ids.length; round += 1) {
    let changed = false
    for (const edge of edges) {
      const next = (level.get(edge.upstream) ?? 0) + 1
      if (next > (level.get(edge.downstream) ?? 0)) {
        level.set(edge.downstream, Math.min(next, ids.length - 1))
        changed = true
      }
    }
    if (!changed) break
  }
  return level
}

/**
 * 构造 ECharts graph series 配置：展示依赖关系，并高亮事件涉及节点。
 */
export function buildTopologyOption(
  topology: Topology,
  incidentNodes: string[] = [],
): Record<string, unknown> {
  const levels = computeLevels(topology)
  const groups = new Map<number, string[]>()
  for (const node of topology.nodes) {
    const lv = levels.get(node.id) ?? 0
    const list = groups.get(lv) ?? []
    list.push(node.id)
    groups.set(lv, list)
  }

  const sortedLevels = [...groups.keys()].sort((a, b) => a - b)
  const nodeById = new Map(topology.nodes.map((node) => [node.id, node]))
  const positions = new Map<string, { x: number; y: number }>()
  const maxRows = Math.max(1, ...sortedLevels.map((lv) => groups.get(lv)?.length ?? 0))
  const rowGap = Math.max(70, Math.min(110, 520 / maxRows))

  for (const lv of sortedLevels) {
    const idsAtLevel = groups.get(lv) ?? []
    idsAtLevel.forEach((id, index) => {
      positions.set(id, {
        x: lv * 190,
        y: (index - (idsAtLevel.length - 1) / 2) * rowGap,
      })
    })
  }

  const incidentSet = new Set(incidentNodes)
  const kinds = [...new Set(topology.nodes.map((node) => node.kind))]
  const categories = kinds.map((kind, index) => ({
    name: kind,
    itemStyle: { color: colorForKind(kind, index) },
  }))

  const nodes = topology.nodes.map((node) => {
    const highlighted = incidentSet.has(node.id) || incidentSet.has(node.name)
    return {
      id: node.id,
      name: node.name || node.id,
      category: kinds.indexOf(node.kind),
      x: positions.get(node.id)?.x ?? 0,
      y: positions.get(node.id)?.y ?? 0,
      symbolSize: highlighted ? 54 : 42,
      itemStyle: {
        borderColor: highlighted ? '#f8fafc' : colorForKind(node.kind, 0),
        borderWidth: highlighted ? 3 : 1,
        shadowBlur: highlighted ? 16 : 0,
        shadowColor: 'rgba(56, 189, 248, 0.65)',
      },
      label: {
        show: true,
        position: 'bottom' as const,
        color: highlighted ? '#f8fafc' : '#cbd5e1',
        fontSize: 12,
        fontWeight: highlighted ? 'bold' : 'normal',
      },
    }
  })

  const links = topology.edges
    .filter((edge) => nodeById.has(edge.upstream) && nodeById.has(edge.downstream))
    .map((edge) => ({
      source: edge.upstream,
      target: edge.downstream,
      value: edge.weight,
      lineStyle: { width: Math.max(1, Math.min(3, edge.weight || 1)) },
    }))

  return {
    tooltip: {
      trigger: 'item' as const,
      formatter: (params: unknown) => {
        const p = params as { dataType?: string; data?: Record<string, unknown>; name?: string }
        if (p.dataType === 'edge') {
          const data = p.data as { source?: string; target?: string; value?: number }
          return `${data.source} → ${data.target}<br/>权重：${data.value ?? '—'}`
        }
        const name = p.name ?? ''
        const node = topology.nodes.find((item) => item.name === name || item.id === name)
        const highlighted = node ? incidentSet.has(node.id) || incidentSet.has(node.name) : false
        return `${name}<br/>类型：${node?.kind ?? '—'}${highlighted ? '<br/><b>本次事件涉及节点</b>' : ''}`
      },
    },
    legend: {
      data: kinds,
      textStyle: { color: '#cbd5e1' },
      top: 0,
      type: 'scroll',
    },
    series: [
      {
        type: 'graph',
        layout: 'none',
        roam: true,
        draggable: false,
        categories,
        data: nodes,
        links,
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: 8,
        lineStyle: {
          color: '#475569',
          curveness: 0.12,
        },
        emphasis: {
          focus: 'adjacency',
          lineStyle: { color: '#38bdf8', width: 2 },
        },
        left: 40,
        right: 40,
        top: 40,
        bottom: 40,
      },
    ],
  }
}
