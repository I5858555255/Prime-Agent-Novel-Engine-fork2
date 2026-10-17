class PacingAdvisor:
    def pre_write_constraints(self, outline: str) -> str:
        return ("【节奏约束】按大纲安排起承转合与情绪曲线，避免平铺直叙；"
                "控制信息密度，每 800–1200 字设一个节拍点：\n" + outline)
