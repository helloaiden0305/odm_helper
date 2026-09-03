/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 */

export const Disclaimer = 'https://www.volcengine.com/docs/6348/68916';
export const ReversoContext = 'https://www.volcengine.com/docs/6348/68918';
export const UserAgreement = 'https://www.volcengine.com/docs/6348/128955';

/**
 * @note 请求的 Python API Proxy Server 地址。
 *       动态使用当前页面的主机名，支持局域网访问
 */
export const AIGC_PROXY_HOST = `http://${window.location.hostname}:3001`;
export const CONTEXT_CONVERSATION_STORAGE_KEY = 'xzy_odm_conversation_id';

export interface IScene {
  icon: string;
  name: string;
  questions: string[];
  agentConfig: Record<string, any>;
  llmConfig: Record<string, any>;
  asrConfig: Record<string, any>;
  ttsConfig: Record<string, any>;
}
