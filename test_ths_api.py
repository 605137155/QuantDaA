"""
测试同花顺热门榜单 API 接口
"""

import requests
import json

def test_ths_api():
    """测试同花顺热门榜单 API"""

    # 主要接口：热门股票小时榜（普通）
    url = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"

    # 请求参数
    params = {
        'stock_type': 'a',
        'type': 'hour',
        'list_type': 'normal'
    }

    # 请求头（模拟浏览器）
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://eq.10jqka.com.cn/frontend/thsTopRank/index.html',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Origin': 'https://eq.10jqka.com.cn',
    }

    print("[Test] 测试同花顺热门榜单 API")
    print("=" * 60)
    print(f"[URL] {url}")
    print(f"[Params] {params}")
    print()

    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)

        print(f"[Status] 状态码: {response.status_code}")
        print(f"[Headers] 响应头:")
        for key, value in response.headers.items():
            if key.lower() in ['content-type', 'set-cookie', 'access-control-allow-origin']:
                print(f"  {key}: {value}")
        print()

        if response.status_code == 200:
            try:
                data = response.json()
                print("[Success] 返回 JSON 数据:")
                print(json.dumps(data, indent=2, ensure_ascii=False)[:2000])
                print("...")

                # 分析数据结构
                print("\n[Analysis] 数据结构分析:")
                if isinstance(data, dict):
                    print(f"  顶层键: {list(data.keys())}")
                    if 'data' in data:
                        print(f"  data 类型: {type(data['data'])}")
                        if isinstance(data['data'], list):
                            print(f"  data 长度: {len(data['data'])}")
                            if len(data['data']) > 0:
                                print(f"  第一条数据: {data['data'][0]}")
                elif isinstance(data, list):
                    print(f"  列表长度: {len(data)}")
                    if len(data) > 0:
                        print(f"  第一条数据: {data[0]}")

            except json.JSONDecodeError:
                print("[Warning] 响应不是 JSON 格式:")
                print(response.text[:500])
        else:
            print(f"[Error] 请求失败: {response.status_code}")
            print(response.text[:500])

    except Exception as e:
        print(f"[Error] 请求异常: {e}")

if __name__ == "__main__":
    test_ths_api()