/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 */

import React, { useRef, useEffect, useMemo } from 'react';
import { useSelector } from 'react-redux';
import { Tag, Spin } from '@arco-design/web-react';
import { RootState } from '@/store';
import Loading from '@/components/Loading/HorizonLoading';
import { isMobile } from '@/utils/utils';
import { useScene } from '@/lib/useCommon';
import USER_AVATAR from '@/assets/img/userAvatar.png';
import styles from './index.module.less';
import AIAvatarReadying from '@/components/AIAvatarLoading';

const lines: (string | React.ReactNode)[] = [];

export interface TextChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  channel?: 'system' | 'text' | 'voice';
  loading?: boolean;
  createdAt: number;
}

type ConversationItem =
  | {
      source: 'voice';
      id: string;
      value: string;
      user: string;
      isInterrupted?: boolean;
      voiceIndex: number;
      createdAt: number;
      order: number;
    }
  | {
      source: 'text';
      id: string;
      value: string;
      user: string;
      role: 'user' | 'assistant';
      loading?: boolean;
      createdAt: number;
      order: number;
    };

function Conversation(
  props: React.HTMLAttributes<HTMLDivElement> & {
    showSubtitle: boolean;
    textMessages?: TextChatMessage[];
    hideVoiceAssistantMessages?: boolean;
    suppressAIThinking?: boolean;
  }
) {
  const {
    className,
    showSubtitle,
    textMessages = [],
    hideVoiceAssistantMessages = false,
    suppressAIThinking = false,
    ...rest
  } = props;
  const room = useSelector((state: RootState) => state.room);
  const { msgHistory, isFullScreen } = room;
  const { userId } = useSelector((state: RootState) => state.room.localUser);
  const { isAITalking, isAIThinking, isUserTalking } = useSelector(
    (state: RootState) => state.room
  );
  const isAIReady = msgHistory.length > 0 || textMessages.length > 0 || !room.isAIGCEnable;
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const { botName, icon, isAvatarScene, name } = useScene();
  const botDisplayName = name || 'XZY ODM研发测试助手';
  const hasLoadingTextMessage = textMessages.some((message) => message.loading);

  const isUserTextLoading = (owner: string) => {
    return owner === userId && isUserTalking;
  };

  const isAITextLoading = (owner: string) => {
    return (owner === botName || owner.includes('voiceChat_')) && isAITalking;
  };

  const conversationItems = useMemo<ConversationItem[]>(() => {
    const voiceItems: ConversationItem[] = (showSubtitle ? msgHistory : [])
      .map((message, index) => {
        const createdAt = message.time ? Date.parse(message.time) : 0;
        return {
          source: 'voice',
          id: `voice-${index}`,
          value: message.value,
          user: message.user,
          isInterrupted: message.isInterrupted,
          voiceIndex: index,
          createdAt: Number.isNaN(createdAt) ? 0 : createdAt,
          order: index,
        };
      })
      .filter((message) => {
        const isUserMsg = message.user === userId;
        const isRobotMsg = message.user === botName || message.user.includes('voiceChat_');
        if (hideVoiceAssistantMessages && isRobotMsg) {
          return false;
        }
        return isUserMsg || isRobotMsg;
      });

    const textItems: ConversationItem[] = (showSubtitle ? textMessages : []).map(
      (message, index) => ({
        source: 'text',
        id: message.id,
        value: message.content,
        user: message.role === 'user' ? userId : botName,
        role: message.role,
        loading: message.loading,
        createdAt: message.createdAt,
        order: msgHistory.length + index,
      })
    );

    return [...voiceItems, ...textItems].sort((left, right) => {
      if (left.createdAt !== right.createdAt) {
        return left.createdAt - right.createdAt;
      }
      return left.order - right.order;
    });
  }, [botName, hideVoiceAssistantMessages, msgHistory, showSubtitle, textMessages, userId]);

  useEffect(() => {
    const frameId = window.requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ block: 'end' });
    });

    return () => window.cancelAnimationFrame(frameId);
  }, [conversationItems, isAIThinking]);

  return (
    <div
      ref={containerRef}
      className={`${styles.conversation} ${className} ${isFullScreen ? styles.fullScreen : ''} ${
        isMobile() ? styles.mobileConversation : ''
      }`}
      style={isAvatarScene && !isAIReady ? { justifyContent: 'center' } : {}}
      {...rest}
    >
      {lines.map((line) => line)}
      {!isAIReady ? (
        <div className={styles.aiReadying}>
          {isAvatarScene ? (
            <AIAvatarReadying />
          ) : (
            <>
              <Spin size={16} className={styles['aiReading-spin']} />
              XZY 研发测试助手准备中, 请稍侯
            </>
          )}
        </div>
      ) : (
        ''
      )}
      {conversationItems.map((message) => {
        const isUserMsg =
          message.source === 'text' ? message.role === 'user' : message.user === userId;
        const isVoiceLoading =
          message.source === 'voice' &&
          isAIReady &&
          (isUserTextLoading(message.user) || isAITextLoading(message.user)) &&
          message.voiceIndex === msgHistory.length - 1;
        const isTextLoading = message.source === 'text' && message.loading;

        return (
          <div
            key={message.id}
            className={styles.mobileLine}
            style={{ justifyContent: isUserMsg && isMobile() ? 'flex-end' : '' }}
          >
            {!isMobile() && (
              <div className={styles.msgName}>
                <div className={styles.avatar}>
                  <img src={isUserMsg ? USER_AVATAR : icon} alt="Avatar" />
                </div>
                {isUserMsg ? '我' : botDisplayName}
              </div>
            )}
            <div className={`${styles.sentence} ${isUserMsg ? styles.user : styles.robot}`}>
              <div className={styles.content}>
                {message.value}
                <div className={styles['loading-wrapper']}>
                  {isVoiceLoading || isTextLoading ? (
                    <Loading gap={3} className={styles.loading} dotClassName={styles.dot} />
                  ) : (
                    ''
                  )}
                </div>
              </div>
              {!isUserMsg && message.source === 'voice' && message.isInterrupted ? (
                <Tag className={styles.interruptTag}>已打断</Tag>
              ) : (
                ''
              )}
            </div>
          </div>
        );
      })}
      {showSubtitle &&
      isAIReady &&
      isAIThinking &&
      !hasLoadingTextMessage &&
      !suppressAIThinking ? (
        <div className={styles.mobileLine}>
          {!isMobile() && (
            <div className={styles.msgName}>
              <div className={styles.avatar}>
                <img src={icon} alt="Avatar" />
              </div>
              {botDisplayName}
            </div>
          )}
          <div className={`${styles.sentence} ${styles.robot} ${styles.thinking}`}>
            <span>XZY 研发测试助手正在分析</span>
            <Loading gap={3} className={styles.loading} dotClassName={styles.dot} />
          </div>
        </div>
      ) : (
        ''
      )}
      <div ref={bottomRef} className={styles.scrollAnchor} />
    </div>
  );
}

export default Conversation;
