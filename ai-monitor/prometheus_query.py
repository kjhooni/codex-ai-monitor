import requests


class PrometheusClient:
    def __init__(self, url):
        self.url = url.rstrip("/")

    def query(self, promql):
        resp = requests.get(f"{self.url}/api/v1/query", params={"query": promql}, timeout=10)
        resp.raise_for_status()
        return resp.json()["data"]["result"]

    def get_node_up(self):
        results = self.query('up{job="node-exporter"}')
        return {r["metric"]["instance"]: float(r["value"][1]) for r in results}

    def get_cpu_percent(self):
        promql = (
            '100 - (avg by(instance)'
            '(irate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
        )
        results = self.query(promql)
        return {r["metric"]["instance"]: float(r["value"][1]) for r in results}

    def get_memory_percent(self):
        promql = (
            "(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100"
        )
        results = self.query(promql)
        return {r["metric"]["instance"]: float(r["value"][1]) for r in results}

    def get_disk_percent(self):
        promql = (
            'max by(instance) ((1 - (node_filesystem_avail_bytes{fstype!="tmpfs"}'
            ' / node_filesystem_size_bytes{fstype!="tmpfs"})) * 100)'
        )
        results = self.query(promql)
        return {r["metric"]["instance"]: float(r["value"][1]) for r in results}

    def get_swap_percent(self):
        # SwapTotal이 0인 노드는 결과에 포함되지 않음
        promql = (
            "(1 - (node_memory_SwapFree_bytes / node_memory_SwapTotal_bytes)) * 100"
        )
        results = self.query(promql)
        return {r["metric"]["instance"]: float(r["value"][1]) for r in results}

    def collect_all(self, nodes_cfg):
        """config.yaml에 등록된 노드별로 메트릭을 모아 {node_name: {metric: value}} 형태로 반환.
        노드명은 node_uname_info 같이 대상 서버 자신이 보내는 메트릭에서 뽑지 않고
        config.yaml의 고정 IP로 매칭한다 - 서버가 다운되면 그 메트릭도 같이 죽어서
        다운된 서버의 이름을 못 찾는 문제를 피하기 위함."""
        node_up = self.get_node_up()
        cpu     = self.get_cpu_percent()
        memory  = self.get_memory_percent()
        disk    = self.get_disk_percent()
        swap    = self.get_swap_percent()

        data = {}
        for node_name, node_cfg in nodes_cfg.items():
            instance = f"{node_cfg['ip']}:9100"
            data[node_name] = {
                "instance": instance,
                "up":       node_up.get(instance, 0),
                "cpu":      cpu.get(instance),
                "memory":   memory.get(instance),
                "disk":     disk.get(instance),
                "swap":     swap.get(instance),
            }
        return data
