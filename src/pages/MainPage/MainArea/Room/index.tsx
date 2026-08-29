/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Message } from '@arco-design/web-react';
import { useSelector } from 'react-redux';
import Conversation from './Conversation';
import type { TextChatMessage } from './Conversation';
import ToolBar from './ToolBar';
import CameraArea from './CameraArea';
import AudioController from './AudioController';
import { isMobile } from '@/utils/utils';
import style from './index.module.less';
import AiAvatarCard from '@/components/AiAvatarCard';
import { RootState } from '@/store';
import UserTag from '@/components/UserTag';
import FullScreenCard from '@/components/FullScreenCard';
import MobileToolBar from '@/pages/Mobile/MobileToolBar';
import { useScene } from '@/lib/useCommon';
import { AIGC_PROXY_HOST } from '@/config';
import RtcClient from '@/lib/RtcClient';
import { COMMAND, INTERRUPT_PRIORITY } from '@/utils/handler';

const TEXT_CHAT_RENDER_INTERVAL_MS = 12;
const TEXT_CHAT_RENDER_IDLE_INTERVAL_MS = 24;
const TEXT_CHAT_RENDER_STEP_CHARS = 1;
const TEXT_MODE_WELCOME_MESSAGE =
  '你好，我是 XZY 研发测试助手。你可以直接输入项目、型号、错误码或日志片段，我会帮你查询 SOP、排查软硬件问题并检索历史缺陷。';

type VoicePreviewEvent =
  | {
      type: 'ready';
    }
  | {
      type: 'start';
      id: string;
      createdAt?: number;
    }
  | {
      type: 'pending';
      id: string;
      content: string;
      createdAt?: number;
    }
  | {
      type: 'delta';
      id: string;
      content: string;
    }
  | {
      type: 'end';
      id: string;
    }
  | {
      type: 'error';
      id: string;
      content?: string;
    };

type VoicePreviewState = {
  id: string;
  buffer: string;
  visible: string;
  done: boolean;
  rendering: boolean;
};

type PendingVoiceQuestion = {
  id: string;
  content: string;
  createdAt: number;
};

const wait = (duration: number) =>
  new Promise<void>((resolve) => {
    window.setTimeout(resolve, duration);
  });

function Room() {
  const room = useSelector((state: RootState) => state.room);
  const { isShowSubtitle, scene, isFullScreen } = room;
  const rtcConfig = room.rtcConfigMap[scene];
  const { isAvatarScene, name, botName } = useScene();
  const [textInput, setTextInput] = useState('');
  const [textSending, setTextSending] = useState(false);
  const [hasVoiceTextPreview, setHasVoiceTextPreview] = useState(false);
  const [pendingVoiceQuestion, setPendingVoiceQuestion] = useState<PendingVoiceQuestion | null>(
    null
  );
  const voicePreviewMapRef = useRef<Record<string, VoicePreviewState>>({});
  const confirmedVoiceLoadingIdRef = useRef<string | null>(null);
  const [textMessages, setTextMessages] = useState<TextChatMessage[]>(() => [
    {
      id: 'text-mode-welcome',
      role: 'assistant',
      channel: 'system',
      content: TEXT_MODE_WELCOME_MESSAGE,
      createdAt: Date.now(),
    },
  ]);
  const isVoiceInputActive = room.isAIGCEnable && Boolean(room.localUser.publishAudio);

  const textHistory = useMemo(
    () =>
      textMessages
        .filter(
          (message) => message.channel === 'text' && !message.loading && message.content.trim()
        )
        .map((message) => ({
          role: message.role,
          content: message.content,
        })),
    [textMessages]
  );

  const updateTextMessage = useCallback((id: string, patch: Partial<TextChatMessage>) => {
    setTextMessages((messages) =>
      messages.map((message) => (message.id === id ? { ...message, ...patch } : message))
    );
  }, []);

  const clearConfirmedVoiceLoading = useCallback(() => {
    const loadingMessageId = confirmedVoiceLoadingIdRef.current;
    if (!loadingMessageId) {
      return;
    }

    confirmedVoiceLoadingIdRef.current = null;
    setTextMessages((messages) => messages.filter((message) => message.id !== loadingMessageId));
  }, []);

  const showConfirmedVoiceLoading = useCallback((pending: PendingVoiceQuestion) => {
    const previousLoadingMessageId = confirmedVoiceLoadingIdRef.current;
    const loadingMessageId = `voice-confirm-loading-${pending.id}`;
    confirmedVoiceLoadingIdRef.current = loadingMessageId;

    setTextMessages((messages) => [
      ...messages.filter(
        (message) => message.id !== previousLoadingMessageId && message.id !== loadingMessageId
      ),
      {
        id: loadingMessageId,
        role: 'assistant',
        channel: 'voice',
        content: 'XZY 研发测试助手正在分析',
        loading: true,
        createdAt: Date.now(),
      },
    ]);
  }, []);

  const renderVoicePreview = useCallback(
    async (previewId: string) => {
      const preview = voicePreviewMapRef.current[previewId];
      if (!preview || preview.rendering) {
        return;
      }

      preview.rendering = true;
      while (!preview.done || preview.visible.length < preview.buffer.length) {
        if (preview.visible.length < preview.buffer.length) {
          const nextLength = Math.min(
            preview.visible.length + TEXT_CHAT_RENDER_STEP_CHARS,
            preview.buffer.length
          );
          preview.visible = preview.buffer.slice(0, nextLength);
          updateTextMessage(preview.id, { content: preview.visible, loading: true });
          // eslint-disable-next-line no-await-in-loop
          await wait(TEXT_CHAT_RENDER_INTERVAL_MS);
        } else {
          // eslint-disable-next-line no-await-in-loop
          await wait(TEXT_CHAT_RENDER_IDLE_INTERVAL_MS);
        }
      }

      updateTextMessage(preview.id, {
        content: preview.visible || '助手暂时没有返回，请稍后重试。',
        loading: false,
      });
      preview.rendering = false;
      if (preview.done) {
        delete voicePreviewMapRef.current[preview.id];
      }
    },
    [updateTextMessage]
  );

  useEffect(() => {
    if (!room.isAIGCEnable || !rtcConfig?.RoomId || !rtcConfig?.UserId) {
      voicePreviewMapRef.current = {};
      setHasVoiceTextPreview(false);
      setPendingVoiceQuestion(null);
      clearConfirmedVoiceLoading();
      return undefined;
    }

    const url = `${AIGC_PROXY_HOST}/api/voice_text_stream?room_id=${encodeURIComponent(
      rtcConfig.RoomId
    )}&user_id=${encodeURIComponent(rtcConfig.UserId)}`;
    const source = new EventSource(url);

    source.onmessage = (messageEvent) => {
      const event = JSON.parse(messageEvent.data) as VoicePreviewEvent;
      if (event.type === 'ready') {
        return;
      }

      if (event.type === 'pending') {
        const content = event.content.trim();
        if (!content) {
          return;
        }

        setPendingVoiceQuestion((pending) => {
          if (!pending) {
            return {
              id: event.id,
              content,
              createdAt: event.createdAt || Date.now(),
            };
          }

          const nextContent = pending.content.includes(content)
            ? pending.content
            : `${pending.content} ${content}`;

          return {
            id: event.id,
            content: nextContent,
            createdAt: pending.createdAt,
          };
        });
        return;
      }

      if (event.type === 'start') {
        clearConfirmedVoiceLoading();
        setHasVoiceTextPreview(true);
        const previewMessageId = `voice-preview-${event.id}`;
        voicePreviewMapRef.current[previewMessageId] = {
          id: previewMessageId,
          buffer: '',
          visible: '',
          done: false,
          rendering: false,
        };
        setTextMessages((messages) => [
          ...messages,
          {
            id: `voice-preview-${event.id}`,
            role: 'assistant',
            channel: 'voice',
            content: 'XZY 研发测试助手正在分析',
            loading: true,
            createdAt: event.createdAt || Date.now(),
          },
        ]);
        return;
      }

      const previewMessageId = `voice-preview-${event.id}`;
      const preview = voicePreviewMapRef.current[previewMessageId];
      if (!preview) {
        return;
      }

      if (event.type === 'delta') {
        preview.buffer += event.content;
        renderVoicePreview(preview.id);
      }

      if (event.type === 'end' || event.type === 'error') {
        if (event.type === 'error' && event.content) {
          preview.buffer += event.content;
        }
        preview.done = true;
        renderVoicePreview(preview.id);
      }
    };

    source.onerror = () => {
      source.close();
    };

    return () => {
      source.close();
    };
  }, [
    clearConfirmedVoiceLoading,
    renderVoicePreview,
    room.isAIGCEnable,
    rtcConfig?.RoomId,
    rtcConfig?.UserId,
  ]);

  const sendQuestionToAssistant = async ({
    question: rawQuestion,
    appendUserMessage = true,
    errorMessage = '文字问答暂时不可用，请稍后重试',
  }: {
    question: string;
    appendUserMessage?: boolean;
    errorMessage?: string;
  }) => {
    const question = rawQuestion.trim();
    if (!question || textSending) {
      return false;
    }

    const createdAt = Date.now();
    const userMessage: TextChatMessage = {
      id: `text-user-${Date.now()}`,
      role: 'user',
      channel: 'text',
      content: question,
      createdAt,
    };
    const assistantMessage: TextChatMessage = {
      id: `text-assistant-${Date.now()}`,
      role: 'assistant',
      channel: 'text',
      content: 'XZY 研发测试助手正在分析',
      loading: true,
      createdAt: createdAt + 1,
    };

    if (appendUserMessage) {
      setTextInput('');
    }
    setTextSending(true);
    setTextMessages((messages) => [
      ...messages,
      ...(appendUserMessage ? [userMessage] : []),
      assistantMessage,
    ]);

    let answerBuffer = '';
    let visibleAnswer = '';
    let streamDone = false;
    let renderLoop: Promise<void> | null = null;

    const updateAssistantMessage = (content: string, loading = true) => {
      setTextMessages((messages) =>
        messages.map((message) =>
          message.id === assistantMessage.id ? { ...message, content, loading } : message
        )
      );
    };

    const renderBufferedAnswer = async () => {
      while (!streamDone || visibleAnswer.length < answerBuffer.length) {
        if (visibleAnswer.length < answerBuffer.length) {
          const nextLength = Math.min(
            visibleAnswer.length + TEXT_CHAT_RENDER_STEP_CHARS,
            answerBuffer.length
          );
          visibleAnswer = answerBuffer.slice(0, nextLength);
          updateAssistantMessage(visibleAnswer, true);
          // eslint-disable-next-line no-await-in-loop
          await wait(TEXT_CHAT_RENDER_INTERVAL_MS);
        } else {
          // eslint-disable-next-line no-await-in-loop
          await wait(TEXT_CHAT_RENDER_IDLE_INTERVAL_MS);
        }
      }
    };

    try {
      const response = await fetch(`${AIGC_PROXY_HOST}/api/text_chat_stream`, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          question,
          history: textHistory,
        }),
      });

      if (!response.ok) {
        throw new Error(`Text chat failed: ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error('Text chat stream is not supported');
      }

      const decoder = new TextDecoder('utf-8');
      renderLoop = renderBufferedAnswer();
      let readerDone = false;

      while (!readerDone) {
        // eslint-disable-next-line no-await-in-loop
        const { done, value } = await reader.read();
        if (done) {
          readerDone = true;
          continue;
        }

        const chunk = decoder.decode(value, { stream: true });
        if (!chunk) {
          continue;
        }

        answerBuffer += chunk;
      }

      const finalChunk = decoder.decode();
      if (finalChunk) {
        answerBuffer += finalChunk;
      }

      streamDone = true;
      await renderLoop;
      updateAssistantMessage(visibleAnswer || '助手暂时没有返回，请稍后重试。', false);
      return true;
    } catch (error) {
      streamDone = true;
      if (renderLoop) {
        await renderLoop.catch(() => undefined);
      }
      console.error(error);
      Message.error(errorMessage);
      updateAssistantMessage('助手暂时没有返回，请稍后重试。', false);
      return false;
    } finally {
      setTextSending(false);
    }
  };

  const sendTextQuestion = () => {
    sendQuestionToAssistant({ question: textInput });
  };

  const confirmVoiceQuestion = async () => {
    if (!pendingVoiceQuestion || textSending || !rtcConfig?.RoomId || !rtcConfig?.UserId) {
      return;
    }

    const pending = pendingVoiceQuestion;
    const question = pendingVoiceQuestion.content;
    setPendingVoiceQuestion(null);
    showConfirmedVoiceLoading(pending);

    try {
      setTextSending(true);
      const response = await fetch(`${AIGC_PROXY_HOST}/api/confirm_voice_question`, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          room_id: rtcConfig.RoomId,
          user_id: rtcConfig.UserId,
          question,
        }),
      });

      if (!response.ok) {
        throw new Error(`Confirm voice question failed: ${response.status}`);
      }

      RtcClient.commandAgent({
        agentName: botName,
        command: COMMAND.EXTERNAL_TEXT_TO_LLM,
        interruptMode: INTERRUPT_PRIORITY.HIGH,
        message: question,
      });
    } catch (error) {
      console.error(error);
      Message.error('语音问题确认发送失败，请稍后重试');
      clearConfirmedVoiceLoading();
      setPendingVoiceQuestion(pending);
    } finally {
      setTextSending(false);
    }
  };

  const cancelVoiceQuestion = () => {
    setPendingVoiceQuestion(null);
  };

  const handleTextInputKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendTextQuestion();
    }
  };

  return (
    <div className={`${style.wrapper} ${isMobile() ? style.mobile : ''}`}>
      {isMobile() ? <div className={style.mobilePlayer} id="mobile-local-player" /> : null}
      {isMobile() ? <MobileToolBar /> : null}
      {isShowSubtitle && !isMobile() ? (
        <UserTag name={name || scene} className={style.subTitleUserTag} />
      ) : null}
      {(isFullScreen || isAvatarScene) && !isMobile() ? (
        <FullScreenCard />
      ) : isMobile() && isShowSubtitle ? null : (
        <AiAvatarCard
          showUserTag={!isShowSubtitle}
          showStatus={!isShowSubtitle}
          className={isShowSubtitle ? style.subtitleAiAvatar : ''}
        />
      )}
      {isMobile() ? null : <CameraArea />}
      <Conversation
        className={style.conversation}
        showSubtitle={isShowSubtitle}
        textMessages={textMessages}
        hideVoiceAssistantMessages={room.isAIGCEnable || hasVoiceTextPreview}
        suppressAIThinking={room.isAIGCEnable}
      />
      {!isVoiceInputActive && !pendingVoiceQuestion ? (
        <div className={style.textChatInput}>
          <textarea
            value={textInput}
            rows={1}
            placeholder="输入问题、型号、错误码或日志片段"
            disabled={textSending}
            onChange={(event) => setTextInput(event.target.value)}
            onKeyDown={handleTextInputKeyDown}
          />
          <button
            type="button"
            disabled={!textInput.trim() || textSending}
            onClick={sendTextQuestion}
          >
            {textSending ? '分析中' : '发送'}
          </button>
        </div>
      ) : null}
      {room.isAIGCEnable && pendingVoiceQuestion ? (
        <div className={style.voiceConfirmPanel}>
          <div className={style.voiceConfirmTitle}>待确认语音问题</div>
          <div className={style.voiceConfirmText}>{pendingVoiceQuestion.content}</div>
          <div className={style.voiceConfirmActions}>
            <button
              type="button"
              className={style.secondary}
              disabled={textSending}
              onClick={cancelVoiceQuestion}
            >
              取消
            </button>
            <button type="button" disabled={textSending} onClick={confirmVoiceQuestion}>
              {textSending ? '分析中' : '确认发送'}
            </button>
          </div>
        </div>
      ) : null}
      <ToolBar className={style.toolBar} />
      <AudioController className={style.controller} />
      <div className={style.declare}>AI 生成内容仅用于研发测试辅助，请结合日志和版本信息复核</div>
    </div>
  );
}

export default Room;
