"""Bounded, nonrecursive analysis of resolved RPK import declarations."""
from collections import defaultdict, deque
from pathlib import Path

from .core import FormatError


def analyze(edges, hashes, max_nodes=4000, max_edges=100000):
    # Resource reference cycles are legal; only pack import declarations belong
    # here. Ambiguous files cannot establish either an edge or a safe load order.
    declarations = [e for e in edges if e['kind'] == 'rpk'
                    and Path(e['source']).suffix.lower() == '.rpk'
                    and not (e.get('optional') and e['status']=='missing')]
    resolved = [e for e in declarations if e['status'] in ('found', 'equivalent_files')]
    unresolved = len(declarations)-len(resolved)
    paths = {e['source'] for e in declarations}
    pairs = set()
    for edge in resolved:
        for target in edge['targets']:
            paths.add(target['file'])
            pairs.add((edge['source'], target['file']))
            if len(pairs) > max_edges:
                raise FormatError('Pack import graph exceeds its edge bound')
        if len(paths) > max_nodes:
            raise FormatError('Pack import graph exceeds its node bound')
    if len(paths) > max_nodes:
        raise FormatError('Pack import graph exceeds its node bound')
    # Equal bytes contain equal internal names and imports. Collapse copies so
    # hash-identical aliases do not produce duplicate cycle reports.
    groups = defaultdict(list)
    for path in sorted(paths):
        groups[('hash', hashes[path]) if path in hashes else ('path', path)].append(path)
    aliases = {path: members[0] for members in groups.values() for path in members}
    files = {members[0]: members for members in groups.values()}
    graph = {node: set() for node in files}; reverse = {node: set() for node in files}
    for source, target in pairs:
        source, target = aliases[source], aliases[target]
        graph[source].add(target); reverse[target].add(source)

    visited, finished = set(), []
    for start in sorted(graph):
        if start in visited:
            continue
        visited.add(start); stack = [(start, iter(sorted(graph[start])))]
        while stack:
            node, pending = stack[-1]
            child = next(pending, None)
            if child is None:
                finished.append(node); stack.pop()
            elif child not in visited:
                visited.add(child); stack.append((child, iter(sorted(graph[child]))))

    visited, components = set(), []
    for start in reversed(finished):
        if start in visited:
            continue
        component, pending = set(), [start]; visited.add(start)
        while pending:
            node = pending.pop(); component.add(node)
            for child in sorted(reverse[node]):
                if child not in visited:
                    visited.add(child); pending.append(child)
        if len(component) > 1 or start in graph[start]:
            components.append(component)

    cycles = []
    for component in sorted(components, key=lambda value: min(value)):
        start = min(component)
        # A shortest return path gives one concrete closed cycle per component,
        # rather than enumerating exponentially many possible cycles.
        parent = {start: None}; pending = deque([start]); end = None
        while pending and end is None:
            node = pending.popleft()
            for child in sorted(graph[node] & component):
                if child == start:
                    end = node; break
                if child not in parent:
                    parent[child] = node; pending.append(child)
        witness = [end]
        while witness[-1] != start:
            witness.append(parent[witness[-1]])
        witness.reverse(); witness.append(start)
        cycles.append({'packs': sorted(component), 'cycle': witness,
                       'files': sorted(path for node in component for path in files[node])})
    return {'nodes': len(graph), 'resolved_edges': sum(map(len, graph.values())),
            'unresolved_declarations': unresolved, 'cycles': cycles,
            'dependency_first_order': finished if not cycles and not unresolved else None,
            'coverage': 'Resolved RPK import declarations only; ambiguous/missing targets are excluded. '
                        'Absent optional imports are excluded. Resource-reference cycles and engine mounting are not evaluated.'}
