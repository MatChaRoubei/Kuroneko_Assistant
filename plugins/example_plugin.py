intent_name = 'weather'

def execute(slots):
    city = slots.get('city', '上海')
    return f'这是一个示例插件：查询 {city} 天气功能暂未实现。'