use crate::prompt::{self, PromptMessage};
use crate::server::ollama_types::ChatMessage;

pub fn format_chat_prompt(
    messages: &[ChatMessage],
    chat_template: Option<&str>,
) -> anyhow::Result<String> {
    let prompt_messages = messages
        .iter()
        .map(|message| PromptMessage {
            role: &message.role,
            content: &message.content,
        })
        .collect::<Vec<_>>();

    prompt::format_chat_prompt(&prompt_messages, chat_template)
}
