"""账号/店铺/渠道服务层 - 解耦 Web API 与 database/Channel。"""
from database.db_manager import db_manager


class AccountService:
    """账号服务的薄封装，转发到 db_manager；登录转发到拼多多登录模块。"""

    # ---- 渠道 ----
    def get_all_channels(self):
        return db_manager.get_all_channels()

    # ---- 店铺 ----
    def get_shops_by_channel(self, channel_name: str):
        return db_manager.get_shops_by_channel(channel_name)

    def get_all_shops(self):
        return db_manager.get_all_shops()

    def get_shop_by_id(self, shop_id: int):
        return db_manager.get_shop_by_id(shop_id)

    def get_shop(self, channel_name: str, shop_id: str):
        return db_manager.get_shop(channel_name, shop_id)

    def add_shop(self, channel_name, shop_id, shop_name, shop_logo, description=None) -> bool:
        return db_manager.add_shop(channel_name, shop_id, shop_name, shop_logo, description)

    # ---- 账号 ----
    def get_accounts_by_shop(self, channel_name, shop_id):
        return db_manager.get_accounts_by_shop(channel_name, shop_id)

    def get_all_accounts_with_details(self):
        return db_manager.get_all_accounts_with_details()

    def get_account(self, channel_name, shop_id, user_id):
        return db_manager.get_account(channel_name, shop_id, user_id)

    def get_account_by_id(self, account_id: int):
        return db_manager.get_account_by_id(account_id)

    def add_account_from_login(self, login_result: dict):
        return db_manager.add_account_from_login(login_result)

    def add_account(self, channel_name, shop_id, user_id, username, password, cookies=None) -> bool:
        return db_manager.add_account(channel_name, shop_id, user_id, username, password, cookies)

    def update_account_info(self, channel_name, shop_id, user_id, username=None, password=None, cookies=None, status=None) -> bool:
        return db_manager.update_account_info(channel_name, shop_id, user_id, username, password, cookies, status)

    def update_account_status(self, channel_name, shop_id, user_id, status) -> bool:
        return db_manager.update_account_status(channel_name, shop_id, user_id, status)

    def update_account_cookies(self, channel_name, shop_id, user_id, cookies) -> bool:
        return db_manager.update_account_cookies(channel_name, shop_id, user_id, cookies)

    def delete_account(self, channel_name, shop_id, user_id) -> bool:
        return db_manager.delete_account(channel_name, shop_id, user_id)

    # ---- 自动回复运行偏好 ----
    def get_auto_reply_enabled(self, account_id: int) -> bool:
        return db_manager.get_auto_reply_enabled(account_id)

    def set_auto_reply_enabled(self, account_id: int, enabled: bool) -> bool:
        return db_manager.set_auto_reply_enabled(account_id, enabled)

    def get_auto_reply_enabled_accounts(self):
        return db_manager.get_auto_reply_enabled_accounts()

    def record_recovery_result(self, account_id: int, error: str | None = None) -> bool:
        return db_manager.record_recovery_result(account_id, error)

    # ---- 登录 ----
    async def login(self, name: str, password: str, headless: bool = False):
        """使用账号密码登录拼多多，返回账号信息 dict 或 False。"""
        from Channel.pinduoduo.pdd_login import login_pdd
        return await login_pdd(name, password, headless=headless)


account_service = AccountService()
